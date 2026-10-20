"""Bearer token middleware for the streamable-HTTP transport."""

import httpx
import pytest
from mcp.server.mcpserver import MCPServer

from mcp_common.auth import BearerTokenMiddleware, load_token, protected_http_app

TOKEN = "t" * 40


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_valid_token_passes():
    async with _client(BearerTokenMiddleware(_ok_app, TOKEN)) as client:
        response = await client.get("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200 and response.text == "ok"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"}],
)
async def test_missing_or_wrong_token_is_401(headers):
    async with _client(BearerTokenMiddleware(_ok_app, TOKEN)) as client:
        response = await client.get("/mcp", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in response.text


async def test_protected_mcp_app_rejects_unauthenticated_requests():
    app = protected_http_app(MCPServer("auth-test"), TOKEN)
    async with _client(app) as client:
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401


def test_short_token_rejected():
    with pytest.raises(ValueError):
        BearerTokenMiddleware(_ok_app, "short")


def test_load_token(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="not set"):
        load_token()
    monkeypatch.setenv("MCP_AUTH_TOKEN", "too-short")
    with pytest.raises(ValueError, match="at least"):
        load_token()
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    assert load_token() == TOKEN
