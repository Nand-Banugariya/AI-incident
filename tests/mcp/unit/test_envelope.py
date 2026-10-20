"""@safe_tool: every outcome is an envelope, failures never leak traces or secrets, limits and timeouts apply."""

import asyncio
import time

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from mcp_common.envelope import ToolResult, redact, register_secret, safe_tool
from mcp_common.errors import InvalidArgument, NotFound, PathNotAllowed, Unreachable
from mcp_common.limits import MAX_BYTES, MAX_ROWS

SOURCE = "test-source"
tool = lambda **kw: safe_tool(source=SOURCE, **kw)  # noqa: E731


def assert_ok_shape(env: dict) -> None:
    assert set(env) == {"ok", "data", "error", "meta"}
    assert env["ok"] is True and env["error"] is None
    assert {"count", "truncated", "duration_ms", "source", "as_of"} <= set(env["meta"])
    assert env["meta"]["source"] == SOURCE
    assert env["meta"]["as_of"].endswith("Z")


def assert_fail(env: dict, code: str, *, retryable: bool) -> None:
    assert env["ok"] is False and env["data"] is None
    assert env["error"]["code"] == code
    assert env["error"]["retryable"] is retryable
    assert env["meta"]["source"] == SOURCE
    assert isinstance(env["meta"]["duration_ms"], int)


async def test_success_list_has_count_and_full_meta():
    @tool()
    async def list_things() -> list[dict]:
        return [{"n": i} for i in range(3)]

    env = await list_things()
    assert_ok_shape(env)
    assert env["data"] == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert env["meta"]["count"] == 3
    assert env["meta"]["truncated"] is False


async def test_sync_tool_runs_in_thread_and_is_wrapped():
    @tool()
    def read_thing(name: str) -> dict:
        return {"name": name}

    env = await read_thing("a")
    assert_ok_shape(env)
    assert env["data"] == {"name": "a"}


async def test_tool_result_meta_and_server_meta_are_merged():
    @tool(meta=lambda: {"repo_head": "abc123", "dirty": False})
    async def read_file() -> ToolResult:
        return ToolResult({"lines": []}, parser="regex")

    env = await read_file()
    assert env["meta"]["repo_head"] == "abc123"
    assert env["meta"]["dirty"] is False
    assert env["meta"]["parser"] == "regex"


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (InvalidArgument("bad window"), "invalid_argument", False),
        (NotFound("no such pod"), "not_found", False),
        (PathNotAllowed(), "path_not_allowed", False),
        (Unreachable("k8s api down"), "unreachable", True),
        (PermissionError("EACCES /etc/shadow"), "permission_denied", False),
        (FileNotFoundError("/home/user/secret/path"), "not_found", False),
        (ConnectionRefusedError("10.0.0.5:5432"), "unreachable", True),
    ],
)
async def test_expected_failures_map_to_codes(exc, code, retryable):
    @tool()
    async def failing():
        raise exc

    env = await failing()
    assert_fail(env, code, retryable=retryable)


async def test_library_messages_are_not_passed_through():
    @tool()
    async def failing():
        raise FileNotFoundError("/home/admin/.ssh/id_rsa")

    env = await failing()
    assert "/home/admin" not in env["error"]["message"]


async def test_unexpected_exception_becomes_internal_without_trace_or_secret():
    @tool()
    async def exploding():
        raise RuntimeError("connect failed: postgresql://investigator:hunter2-pass@db:5432/x")

    env = await exploding()
    assert_fail(env, "internal", retryable=False)
    message = env["error"]["message"]
    assert "hunter2" not in message
    assert "Traceback" not in message and "RuntimeError" not in message
    assert "ref " in message  # caller gets a reference id to find the server-side log


async def test_tool_error_message_is_redacted():
    register_secret("s3cr3t-value-xyz")

    @tool()
    async def failing():
        raise InvalidArgument("rejected: s3cr3t-value-xyz password=abc token: def")

    message = (await failing())["error"]["message"]
    assert "s3cr3t-value-xyz" not in message
    assert "abc" not in message and "def" not in message


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("postgresql://user:pa55word@host/db", "pa55word"),
        ("Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi"),
        ("api_key=sk-12345", "sk-12345"),
    ],
)
def test_redact(raw, leaked):
    assert leaked not in redact(raw)


def test_redact_leaves_ordinary_text_alone():
    text = "password authentication failed for user investigator"
    assert redact(text) == text


async def test_slow_async_backend_times_out_retryable():
    @tool(timeout_s=0.05)
    async def slow():
        await asyncio.sleep(5)

    started = time.perf_counter()
    env = await slow()
    assert time.perf_counter() - started < 2
    assert_fail(env, "timeout", retryable=True)


async def test_slow_sync_backend_times_out_retryable():
    @tool(timeout_s=0.05)
    def slow():
        time.sleep(1)

    started = time.perf_counter()
    env = await slow()
    assert time.perf_counter() - started < 0.9  # abandoned, not awaited
    assert_fail(env, "timeout", retryable=True)


async def test_oversized_result_is_truncated_with_hint():
    @tool()
    async def big() -> list[dict]:
        return [{"text": "x" * 1000} for _ in range(MAX_ROWS)]

    env = await big()
    assert env["ok"] is True
    assert env["meta"]["truncated"] is True
    assert env["meta"]["count"] == MAX_ROWS
    assert len(env["data"]) < MAX_ROWS
    assert "narrow" in env["meta"]["hint"]


async def test_untrimmable_result_is_result_too_large():
    @tool()
    async def huge() -> dict:
        return {f"k{i}": "x" * 1000 for i in range(MAX_BYTES // 500)}

    assert_fail(await huge(), "result_too_large", retryable=False)


async def test_registered_on_mcp_server_keeps_input_schema_and_returns_envelope():
    server = MCPServer("envelope-test")

    @server.tool()
    @tool()
    async def greet(name: str, times: int = 1) -> list[str]:
        if times < 1:
            raise InvalidArgument("times must be >= 1")
        return [f"hi {name}"] * times

    async with Client(server) as client:
        listed = {t.name: t for t in (await client.list_tools()).tools}
        assert set(listed["greet"].input_schema["properties"]) == {"name", "times"}
        assert listed["greet"].input_schema["required"] == ["name"]

        result = await client.call_tool("greet", {"name": "bob", "times": 2})
        assert result.structured_content["ok"] is True
        assert result.structured_content["data"] == ["hi bob", "hi bob"]

        bad = await client.call_tool("greet", {"name": "bob", "times": 0})
        assert bad.structured_content["error"]["code"] == "invalid_argument"
