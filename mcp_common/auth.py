"""Shared bearer token for the streamable-HTTP transport.

Servers bind to 127.0.0.1 or the internal Docker network; the token additionally stops any other process on
the host from calling them. It is a minimum, not a substitute for TLS once servers run on separate hosts.
"""

import hmac
import json
import os
from typing import Any, Awaitable, Callable

MIN_TOKEN_LENGTH = 32
DEFAULT_TOKEN_ENV = "MCP_AUTH_TOKEN"

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _check_token(token: str) -> str:
    if not token or len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"MCP auth token must be at least {MIN_TOKEN_LENGTH} characters")
    return token


def load_token(env_var: str = DEFAULT_TOKEN_ENV) -> str:
    """Reads the server's token at startup; a server never runs over HTTP without one."""
    token = os.environ.get(env_var, "")
    if not token:
        raise ValueError(f"{env_var} is not set; refusing to serve MCP over HTTP without a bearer token")
    return _check_token(token)


class BearerTokenMiddleware:
    """ASGI middleware rejecting any HTTP request without `Authorization: Bearer <token>` (401)."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self._expected = f"Bearer {_check_token(token)}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):  # lifespan events carry no headers
            await self.app(scope, receive, send)
            return
        provided = dict(scope.get("headers") or []).get(b"authorization", b"")
        if hmac.compare_digest(provided, self._expected):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"error": "unauthorized"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def protected_http_app(server: Any, token: str, *, host: str = "127.0.0.1", path: str = "/mcp") -> ASGIApp:
    """The server's streamable-HTTP ASGI app wrapped in the bearer check. Serve it with uvicorn."""
    return BearerTokenMiddleware(server.streamable_http_app(streamable_http_path=path, host=host), token)
