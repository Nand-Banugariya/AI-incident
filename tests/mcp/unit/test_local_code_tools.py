"""Local Code MCP tools (E1), called in-process through the MCP client."""

import shutil

import pytest
from mcp import Client

from tests.mcp.conftest import call, make_server

READ_ONLY_TOOLS = {"list_files", "read_file", "search_code", "get_method", "find_references", "describe_capabilities"}


async def test_exposes_exactly_the_read_only_tools(repo):
    async with Client(make_server(repo)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == READ_ONLY_TOOLS


async def test_describe_capabilities(repo):
    env = await call(make_server(repo), "describe_capabilities")
    assert env["ok"] is True
    assert env["data"]["server"] == "local-code"
    assert env["data"]["capabilities"] == ["source_code"]


async def test_meta_has_repo_head_and_dirty_for_git(git_repo):
    env = await call(make_server(git_repo), "describe_capabilities")
    assert len(env["meta"]["repo_head"]) == 40
    assert env["meta"]["dirty"] is False  # untracked files don't count; tracked changes do

    (git_repo.root / "src" / "orders.py").write_text("changed\n", encoding="utf-8")
    server = make_server(git_repo)  # new instance: no cached HEAD state
    assert (await call(server, "describe_capabilities"))["meta"]["dirty"] is True


async def test_meta_repo_head_is_null_outside_git(tmp_path):
    from tests.mcp.conftest import build_repo

    layout = build_repo(tmp_path, git=False)
    env = await call(make_server(layout), "describe_capabilities")
    assert env["meta"]["repo_head"] is None and env["meta"]["dirty"] is None


# --- list_files -----------------------------------------------------------------------------------

async def test_list_files_respects_gitignore_and_reports_sizes(git_repo):
    env = await call(make_server(git_repo), "list_files")
    paths = {entry["path"] for entry in env["data"]}
    assert {"src/orders.py", "src/Billing.cs", "src/app.js", ".gitignore", "src/untracked_new.py"} <= paths
    assert "secrets.env" not in paths and "build/out.txt" not in paths
    assert all(entry["size"] >= 0 for entry in env["data"])


async def test_list_files_plain_dir_skips_default_noise(tmp_path):
    from tests.mcp.conftest import build_repo

    layout = build_repo(tmp_path, git=False)
    (layout.root / "node_modules" / "lib").mkdir(parents=True)
    (layout.root / "node_modules" / "lib" / "index.js").write_text("x", encoding="utf-8")
    paths = {e["path"] for e in (await call(make_server(layout), "list_files"))["data"]}
    assert "src/orders.py" in paths
    assert not any(p.startswith("node_modules/") for p in paths)


@pytest.mark.parametrize(("glob", "expected"), [("*.py", {"src/orders.py"}), ("src/*.cs", {"src/Billing.cs"})])
async def test_list_files_glob(git_repo, glob, expected):
    env = await call(make_server(git_repo), "list_files", dir="src", glob=glob)
    assert {e["path"] for e in env["data"]} - {"src/untracked_new.py"} == expected


async def test_list_files_missing_dir_is_not_found(repo):
    env = await call(make_server(repo), "list_files", dir="nope")
    assert env["error"]["code"] == "not_found"


# --- read_file ------------------------------------------------------------------------------------

async def test_read_file_whole(repo):
    env = await call(make_server(repo), "read_file", path="src/app.js")
    data = env["data"]
    assert data["path"] == "src/app.js"
    assert data["total_lines"] == 4
    assert data["lines"][0] == {"n": 1, "text": "export function handle(req) {"}


async def test_read_file_range(repo):
    data = (await call(make_server(repo), "read_file", path="src/orders.py", start_line=9, end_line=10))["data"]
    assert [line["n"] for line in data["lines"]] == [9, 10]
    assert data["lines"][0]["text"] == "class OrderService:"
    assert (data["start_line"], data["end_line"]) == (9, 10)


async def test_read_file_long_file_is_truncated_with_hint(repo):
    env = await call(make_server(repo, max_file_bytes=500_000), "read_file", path="big.txt")
    assert env["ok"] is True
    assert env["meta"]["truncated"] is True
    assert env["data"]["total_lines"] == 20_001
    assert len(env["data"]["lines"]) <= 200
    assert "narrow" in env["meta"]["hint"]


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"path": "src/missing.py"}, "not_found"),
        ({"path": "src"}, "invalid_argument"),
        ({"path": "image.bin"}, "invalid_argument"),
        ({"path": "big.txt"}, "result_too_large"),
        ({"path": "src/app.js", "start_line": 0}, "invalid_argument"),
        ({"path": "src/app.js", "start_line": 3, "end_line": 2}, "invalid_argument"),
        ({"path": "src/app.js", "start_line": 99}, "invalid_argument"),
    ],
)
async def test_read_file_errors(repo, args, code):
    env = await call(make_server(repo), "read_file", **args)
    assert env["ok"] is False and env["error"]["code"] == code


# --- search_code / find_references ----------------------------------------------------------------

async def test_search_code_regex(repo):
    env = await call(make_server(repo), "search_code", pattern=r"def \w+\(self")
    hits = {(h["path"], h["line"]) for h in env["data"]}
    assert ("src/orders.py", 10) in hits and ("src/orders.py", 14) in hits
    assert env["meta"]["engine"] == "python"


async def test_search_code_skips_binary_and_ignored(git_repo):
    env = await call(make_server(git_repo), "search_code", pattern="binary|ignored-file|generated")
    assert env["data"] == []


async def test_search_code_glob_and_case(repo):
    env = await call(make_server(repo), "search_code", pattern="orderid", glob="*.cs", case_sensitive=False)
    assert env["data"] and all(h["path"] == "src/Billing.cs" for h in env["data"])
    sensitive = await call(make_server(repo), "search_code", pattern="orderid", glob="*.cs")
    assert sensitive["data"] == []


async def test_search_code_fixed_strings(repo):
    env = await call(make_server(repo), "search_code", pattern="helper(r)", fixed_strings=True)
    assert [h["line"] for h in env["data"]] == [16]


async def test_search_code_max_results(repo):
    env = await call(make_server(repo), "search_code", pattern="e", max_results=3)
    assert len(env["data"]) == 3 and env["meta"]["limit_reached"] is True


@pytest.mark.parametrize(
    "args",
    [{"pattern": ""}, {"pattern": "(unclosed"}, {"pattern": "x" * 1001}, {"pattern": "x", "max_results": 0},
     {"pattern": "x", "max_results": 201}],
)
async def test_search_code_invalid_arguments(repo, args):
    env = await call(make_server(repo), "search_code", **args)
    assert env["error"]["code"] == "invalid_argument"


async def test_find_references_is_whole_word_and_textual(repo):
    env = await call(make_server(repo), "find_references", symbol="helper")
    assert env["meta"]["match"] == "textual"
    assert {(r["path"], r["line"]) for r in env["data"]} == {("src/orders.py", 5), ("src/orders.py", 16)}
    assert all(r["match"] == "textual" for r in env["data"])


async def test_find_references_rejects_non_identifiers(repo):
    env = await call(make_server(repo), "find_references", symbol="a|b")
    assert env["error"]["code"] == "invalid_argument"


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is not installed")
async def test_ripgrep_engine_matches_python_engine(git_repo):
    rg = await call(make_server(git_repo, use_ripgrep=True), "search_code", pattern=r"def \w+")
    py = await call(make_server(git_repo, use_ripgrep=False), "search_code", pattern=r"def \w+")
    assert rg["meta"]["engine"] == "ripgrep"
    key = lambda env: sorted((h["path"], h["line"]) for h in env["data"])  # noqa: E731
    assert key(rg) == key(py)


# --- get_method -----------------------------------------------------------------------------------

async def test_get_method_python(repo):
    env = await call(make_server(repo), "get_method", file="src/orders.py", name="search")
    data = env["data"]
    assert env["meta"]["parser"] == "python-ast"
    assert data["name"] == "OrderService.search"
    assert (data["start_line"], data["end_line"]) == (13, 16)  # includes the decorator
    assert data["body"].startswith("    @functools.lru_cache")


async def test_get_method_ambiguous_then_qualified(repo):
    ambiguous = await call(make_server(repo), "get_method", file="src/orders.py", name="save")
    assert ambiguous["error"]["code"] == "invalid_argument"
    assert "OrderService.save" in ambiguous["error"]["message"]
    assert "AuditService.save" in ambiguous["error"]["message"]

    qualified = await call(make_server(repo), "get_method", file="src/orders.py", name="AuditService.save")
    assert qualified["data"]["start_line"] == 23


async def test_get_method_csharp(repo):
    env = await call(make_server(repo), "get_method", file="src/Billing.cs", name="CalculateTotal")
    data = env["data"]
    assert env["meta"]["parser"] == "regex"
    assert data["name"] == "Billing.CalculateTotal"
    assert data["body"].lstrip().startswith('[HttpGet("total")]')
    assert data["body"].rstrip().endswith("}")
    assert "return 0m;" in data["body"]


async def test_get_method_not_found(repo):
    env = await call(make_server(repo), "get_method", file="src/orders.py", name="nope")
    assert env["error"]["code"] == "not_found"


async def test_get_method_unsupported_extension(repo):
    env = await call(make_server(repo), "get_method", file=".gitignore", name="x")
    assert env["error"]["code"] == "invalid_argument"
