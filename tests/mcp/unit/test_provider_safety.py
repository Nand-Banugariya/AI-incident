"""E2: no Local Code tool can read, list or search anything outside the repository root."""

import os

import pytest

from tests.mcp.conftest import SECRET_MARKER, call, make_server

IS_WINDOWS = os.name == "nt"

# (tool, argument holding the path)
PATH_TOOLS = [("read_file", "path", {}), ("list_files", "dir", {}), ("get_method", "file", {"name": "x"})]


def _escapes(layout) -> list[str]:
    cases = [
        "../outside/secret.txt",
        "../../etc/passwd",
        "src/../../outside/secret.txt",
        str(layout.outside / "secret.txt"),
        str(layout.outside),
        "src/orders.py\x00.txt",
    ]
    if IS_WINDOWS:
        cases += ["Z:\\Windows\\win.ini", "Z:secret.txt", "\\\\server\\share\\x", "src\\orders.py:hidden"]
    else:
        cases += ["/etc/passwd"]
    return cases


def _link(layout, name: str, target, *, directory: bool) -> str:
    """Creates a symlink (or, on Windows without the privilege, a junction for directories)."""
    link = layout.root / name
    try:
        link.symlink_to(target, target_is_directory=directory)
        return name
    except (OSError, NotImplementedError):
        if IS_WINDOWS and directory:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return name
        pytest.skip("Creating symlinks needs Developer Mode or admin rights on Windows")


@pytest.mark.parametrize(("tool", "arg", "extra"), PATH_TOOLS)
async def test_every_escape_is_path_not_allowed(repo, tool, arg, extra):
    server = make_server(repo)
    for raw in _escapes(repo):
        env = await call(server, tool, **{arg: raw}, **extra)
        assert env["ok"] is False, f"{tool}({raw!r}) escaped"
        assert env["error"]["code"] == "path_not_allowed", f"{tool}({raw!r}) -> {env['error']}"
        assert SECRET_MARKER not in str(env)


async def test_symlinked_file_pointing_outside_is_refused(repo):
    name = _link(repo, "evil_link.txt", repo.outside / "secret.txt", directory=False)
    env = await call(make_server(repo), "read_file", path=name)
    assert env["error"]["code"] == "path_not_allowed"


@pytest.mark.parametrize(("tool", "arg", "extra"), PATH_TOOLS)
async def test_linked_directory_pointing_outside_is_refused(repo, tool, arg, extra):
    name = _link(repo, "evil_dir", repo.outside, directory=True)
    target = f"{name}/secret.txt" if tool != "list_files" else name
    env = await call(make_server(repo), tool, **{arg: target}, **extra)
    assert env["error"]["code"] == "path_not_allowed"


async def test_enumeration_and_search_never_surface_outside_files(repo):
    _link(repo, "evil_dir", repo.outside, directory=True)
    server = make_server(repo)

    listed = await call(server, "list_files")
    assert not any("secret.txt" in entry["path"] for entry in listed["data"])

    found = await call(server, "search_code", pattern=SECRET_MARKER, fixed_strings=True)
    assert found["ok"] is True and found["data"] == []

    refs = await call(server, "find_references", symbol="SECRET")
    assert SECRET_MARKER not in str(refs)


async def test_normal_in_root_read_succeeds(repo):
    env = await call(make_server(repo), "read_file", path="src/orders.py")
    assert env["ok"] is True and env["data"]["path"] == "src/orders.py"


async def test_results_never_expose_host_paths(repo):
    server = make_server(repo)
    for tool, args in [("list_files", {}), ("search_code", {"pattern": "def"}),
                       ("read_file", {"path": str(repo.root / "src" / "app.js")})]:
        env = await call(server, tool, **args)
        assert str(repo.root) not in str(env["data"]), tool
