"""The response envelope every MCP tool returns, and `@safe_tool`, which guarantees it.

    {"ok": true,  "data": ..., "error": null, "meta": {count, truncated, duration_ms, source, as_of, ...}}
    {"ok": false, "data": null, "error": {code, message, retryable}, "meta": {duration_ms, source}}

Expected failures come back as `ok: false`; a tool never raises to the MCP layer. A stack trace or a
credential never reaches `error.message`: unexpected exceptions are logged server-side under a reference id
and the caller gets only that id.
"""

import functools
import inspect
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import anyio
import anyio.to_thread

from mcp_common.errors import RETRYABLE_CODES, ErrorCode, ToolError
from mcp_common.limits import DEFAULT_TIMEOUT_S, cap_result

logger = logging.getLogger(__name__)

Envelope = dict[str, Any]

_MAX_MESSAGE_CHARS = 500
# user:password@ in URLs/DSNs, and key=value / key: value pairs whose key looks like a secret
_URL_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_SECRET_PAIR = re.compile(
    r"(?i)\b(?P<key>pass(?:word|wd)?|secret|token|api[_-]?key|authorization)\b(?P<sep>\s*[=:]\s*)\S+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_REDACTED = "[redacted]"

# Exact secret values a server has resolved (registered by mcp_common.secrets); scrubbed from every message
_known_secrets: set[str] = set()


def register_secret(value: str) -> None:
    """Marks a resolved credential so it is scrubbed if it ever appears in an error message."""
    if value and len(value) >= 4:
        _known_secrets.add(value)


def redact(message: str) -> str:
    for secret in sorted(_known_secrets, key=len, reverse=True):
        message = message.replace(secret, _REDACTED)
    message = _URL_USERINFO.sub(rf"\g<scheme>{_REDACTED}@", message)
    message = _BEARER.sub(f"Bearer {_REDACTED}", message)
    message = _SECRET_PAIR.sub(rf"\g<key>\g<sep>{_REDACTED}", message)
    return message[:_MAX_MESSAGE_CHARS]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ToolResult:
    """What a tool returns when it has metadata to add (e.g. `repo_head`); plain data is fine otherwise."""

    def __init__(self, data: Any, **meta: Any):
        self.data = data
        self.meta = meta


def ok(data: Any, *, source: str, duration_ms: int, count: int | None = None, truncated: bool = False,
       **meta: Any) -> Envelope:
    return {
        "ok": True,
        "data": data,
        "error": None,
        "meta": {
            "count": count,
            "truncated": truncated,
            "duration_ms": duration_ms,
            "source": source,
            "as_of": utc_now_iso(),
            **meta,
        },
    }


def fail(code: ErrorCode, message: str, *, source: str, duration_ms: int, retryable: bool | None = None) -> Envelope:
    return {
        "ok": False,
        "data": None,
        "error": {
            "code": code.value,
            "message": redact(message),
            "retryable": code in RETRYABLE_CODES if retryable is None else retryable,
        },
        "meta": {"duration_ms": duration_ms, "source": source},
    }


def _classify(exc: BaseException, tool_name: str) -> tuple[ErrorCode, str, bool | None]:
    """Maps an exception to (code, caller-safe message, retryable). Library messages are never passed through."""
    if isinstance(exc, ToolError):
        return exc.code, exc.message, exc.retryable
    if isinstance(exc, TimeoutError):
        return ErrorCode.TIMEOUT, f"'{tool_name}' did not complete in time", True
    if isinstance(exc, PermissionError):
        return ErrorCode.PERMISSION_DENIED, f"'{tool_name}' was denied access to the resource", False
    if isinstance(exc, FileNotFoundError):
        return ErrorCode.NOT_FOUND, "The requested resource does not exist", False
    if isinstance(exc, ConnectionError):
        return ErrorCode.UNREACHABLE, "The backing service could not be reached", True
    ref = uuid.uuid4().hex[:12]
    logger.error("Unexpected error in tool '%s' (ref %s)", tool_name, ref, exc_info=exc)
    return ErrorCode.INTERNAL, f"Internal error in '{tool_name}'; see server logs (ref {ref})", False


def safe_tool(
    *,
    source: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    meta: Callable[[], dict[str, Any]] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Awaitable[Envelope]]]:
    """Wraps a tool function (sync or async) so it always returns an envelope.

    Enforces `timeout_s`, maps exceptions to the fixed error codes, applies the size limits and fills `meta`.
    `meta()` adds server-wide fields to successful results (e.g. the Local Code server's `repo_head`).
    The wrapper keeps the tool's parameters, so MCP still derives the right input schema from it.
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Awaitable[Envelope]]:
        is_async = inspect.iscoroutinefunction(fn)
        name = fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Envelope:
            started = time.perf_counter()
            elapsed = lambda: int((time.perf_counter() - started) * 1000)  # noqa: E731
            try:
                with anyio.fail_after(timeout_s):
                    if is_async:
                        result = await fn(*args, **kwargs)
                    else:
                        # A thread can't be killed: on timeout it is abandoned and finishes in the background
                        result = await anyio.to_thread.run_sync(
                            functools.partial(fn, *args, **kwargs), abandon_on_cancel=True
                        )
                extra = dict(meta()) if meta else {}
                if isinstance(result, ToolResult):
                    extra.update(result.meta)
                    result = result.data
                capped = cap_result(result)
                if capped.hint:
                    extra["hint"] = capped.hint
                return ok(capped.data, source=source, duration_ms=elapsed(), count=capped.count,
                          truncated=capped.truncated, **extra)
            except Exception as exc:
                code, message, retryable = _classify(exc, name)
                if code is not ErrorCode.INTERNAL:
                    logger.info("Tool '%s' failed: %s", name, code.value)
                return fail(code, message, source=source, duration_ms=elapsed(), retryable=retryable)

        # MCP builds the output schema from the return annotation: advertise the envelope, not the raw data
        wrapper.__signature__ = inspect.signature(fn, eval_str=True).replace(return_annotation=dict[str, Any])
        return wrapper

    return decorator
