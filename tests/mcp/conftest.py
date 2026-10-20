"""Fixtures for MCP server tests: a throwaway repository (git and non-git) with a secret outside it."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from mcp_servers.local_code.settings import LocalCodeSettings
from mcp_servers.local_code.tools import build_server

SECRET_MARKER = "TOP-SECRET-OUTSIDE-THE-REPO"

PY_SOURCE = '''"""Order service."""
import functools


def helper(x):
    return x * 2


class OrderService:
    def __init__(self, repo):
        self.repo = repo

    @functools.lru_cache
    def search(self, email):
        rows = self.repo.find(email)
        return [helper(r) for r in rows]

    async def save(self, order):
        await self.repo.save(order)


class AuditService:
    def save(self, entry):
        return entry
'''

CS_SOURCE = '''using System;

namespace Demo.Billing
{
    public interface IBilling
    {
        Task Save();
    }

    public class Billing : BaseService
    {
        private readonly List<int> _items = new List<int>();

        public Billing(IRepo repo) : base(repo)
        {
        }

        public int Count() => _items.Count;

        [HttpGet("total")]
        public async Task<decimal> CalculateTotal(int orderId)
        {
            var label = "not a brace: } {";
            // a comment with a brace }
            if (orderId > 0)
            {
                return await Sum(orderId);
            }
            return 0m;
        }

        private Task<decimal> Sum(int id) { return Task.FromResult(1m); }
    }
}
'''

JS_SOURCE = '''export function handle(req) {
  const msg = `template ${req.id} }`;
  return { ok: true, msg };
}
'''


@dataclass
class RepoLayout:
    root: Path
    outside: Path
    is_git: bool


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", "-c", "core.autocrlf=false",
         *args],
        cwd=root, check=True, capture_output=True,
    )


def build_repo(tmp_path: Path, *, git: bool) -> RepoLayout:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "orders.py").write_text(PY_SOURCE, encoding="utf-8")
    (root / "src" / "Billing.cs").write_text(CS_SOURCE, encoding="utf-8")
    (root / "src" / "app.js").write_text(JS_SOURCE, encoding="utf-8")
    (root / "image.bin").write_bytes(b"\x89PNG\x00\x00binary\x00data")
    (root / ".gitignore").write_text("secrets.env\nbuild/\n", encoding="utf-8")
    (root / "secrets.env").write_text("API_TOKEN=ignored-file\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "out.txt").write_text("generated\n", encoding="utf-8")
    (root / "big.txt").write_text("x" * 200 + "\n" * 1 + ("line\n" * 20_000), encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(f"{SECRET_MARKER}\n", encoding="utf-8")

    if git:
        _git(root, "init", "-q")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "initial")
        (root / "src" / "untracked_new.py").write_text("def brand_new():\n    pass\n", encoding="utf-8")
    return RepoLayout(root=root, outside=outside, is_git=git)


@pytest.fixture(params=[True, False], ids=["git", "plain"])
def repo(request, tmp_path) -> RepoLayout:
    return build_repo(tmp_path, git=request.param)


@pytest.fixture
def git_repo(tmp_path) -> RepoLayout:
    return build_repo(tmp_path, git=True)


def make_server(layout: RepoLayout, **overrides):
    overrides.setdefault("use_ripgrep", False)
    overrides.setdefault("max_file_bytes", 50_000)
    return build_server(LocalCodeSettings.for_root(layout.root, **overrides))


async def call(server, tool: str, **args: Any) -> dict:
    async with Client(server) as client:
        result = await client.call_tool(tool, args)
    return result.structured_content
