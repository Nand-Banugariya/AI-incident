"""resolve_safe_path: every escape is refused with PathNotAllowed; normal in-root paths resolve."""

import os
from pathlib import Path

import pytest

from mcp_common.errors import PathNotAllowed
from mcp_common.paths import display_path, resolve_roots, resolve_safe_path

IS_WINDOWS = os.name == "nt"


@pytest.fixture
def layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('hi')\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n")
    return resolve_roots([str(repo)]), repo.resolve(), outside.resolve()


def test_relative_in_root_path_resolves(layout):
    roots, repo, _ = layout
    path = resolve_safe_path("src/app.py", roots)
    assert path == repo / "src" / "app.py"
    assert path.read_text() == "print('hi')\n"


def test_root_itself_and_absolute_in_root_allowed(layout):
    roots, repo, _ = layout
    assert resolve_safe_path(".", roots) == repo
    assert resolve_safe_path(str(repo / "src"), roots) == repo / "src"


def test_missing_file_inside_root_is_returned_for_not_found(layout):
    roots, repo, _ = layout
    assert resolve_safe_path("src/missing.py", roots) == repo / "src" / "missing.py"


def test_dotdot_inside_root_that_stays_inside_is_allowed(layout):
    roots, repo, _ = layout
    assert resolve_safe_path("src/../src/app.py", roots) == repo / "src" / "app.py"


@pytest.mark.parametrize(
    "raw",
    [
        "../outside/secret.txt",
        "../../etc/passwd",
        "src/../../outside/secret.txt",
        "..",
        "src/app.py\x00.txt",
    ],
)
def test_escapes_refused(layout, raw):
    roots, _, _ = layout
    with pytest.raises(PathNotAllowed):
        resolve_safe_path(raw, roots)


def test_absolute_path_outside_root_refused(layout):
    roots, _, outside = layout
    with pytest.raises(PathNotAllowed):
        resolve_safe_path(str(outside / "secret.txt"), roots)


def test_sibling_with_shared_prefix_refused(layout, tmp_path):
    roots, _, _ = layout
    (tmp_path / "repo-evil").mkdir()
    with pytest.raises(PathNotAllowed):
        resolve_safe_path(str(tmp_path / "repo-evil"), roots)


def test_symlink_inside_root_pointing_outside_refused(layout):
    roots, repo, outside = layout
    link = repo / "src" / "link.txt"
    try:
        link.symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks needs Developer Mode or admin rights on Windows")
    with pytest.raises(PathNotAllowed):
        resolve_safe_path("src/link.txt", roots)


def test_symlinked_dir_inside_root_pointing_outside_refused(layout):
    roots, repo, outside = layout
    link = repo / "linkdir"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks needs Developer Mode or admin rights on Windows")
    with pytest.raises(PathNotAllowed):
        resolve_safe_path("linkdir/secret.txt", roots)


@pytest.mark.skipif(not IS_WINDOWS, reason="Junctions are Windows-only")
def test_junction_inside_root_pointing_outside_refused(layout):
    import _winapi

    roots, repo, outside = layout
    _winapi.CreateJunction(str(outside), str(repo / "junction"))  # needs no special privilege
    with pytest.raises(PathNotAllowed):
        resolve_safe_path("junction/secret.txt", roots)


@pytest.mark.skipif(not IS_WINDOWS, reason="Drive letters are Windows-only")
@pytest.mark.parametrize("raw", ["Z:\\Windows\\win.ini", "Z:secret.txt", "\\\\server\\share\\x.txt"])
def test_other_drive_and_unc_refused(layout, raw):
    roots, _, _ = layout
    with pytest.raises(PathNotAllowed):
        resolve_safe_path(raw, roots)


@pytest.mark.skipif(not IS_WINDOWS, reason="Alternate data streams are Windows-only")
def test_alternate_data_stream_refused(layout):
    roots, _, _ = layout
    with pytest.raises(PathNotAllowed):
        resolve_safe_path("src/app.py:hidden", roots)


@pytest.mark.skipif(not IS_WINDOWS, reason="Case-insensitive paths are Windows behaviour")
def test_case_variant_of_root_is_still_inside(layout):
    roots, repo, _ = layout
    variant = str(repo).swapcase()
    assert resolve_safe_path(variant + "\\SRC\\APP.PY", roots).read_text() == "print('hi')\n"


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX absolute path")
def test_etc_passwd_absolute_refused(layout):
    roots, _, _ = layout
    with pytest.raises(PathNotAllowed):
        resolve_safe_path("/etc/passwd", roots)


def test_multiple_roots(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    roots = resolve_roots([str(a), str(b)])
    assert resolve_safe_path(str(b / "x.py"), roots) == b.resolve() / "x.py"
    assert resolve_safe_path("x.py", roots) == a.resolve() / "x.py"  # relative paths resolve against the first root


def test_resolve_roots_rejects_missing_or_empty(tmp_path):
    with pytest.raises(ValueError):
        resolve_roots([])
    with pytest.raises(FileNotFoundError):
        resolve_roots([str(tmp_path / "nope")])


def test_no_roots_refuses_everything():
    with pytest.raises(PathNotAllowed):
        resolve_safe_path("anything", [])


def test_display_path_is_relative_posix(layout):
    roots, repo, outside = layout
    assert display_path(repo / "src" / "app.py", roots) == "src/app.py"
    assert display_path(repo, roots) == "."
    with pytest.raises(PathNotAllowed):
        display_path(outside, roots)


def test_display_path_never_contains_host_prefix(layout):
    roots, repo, _ = layout
    shown = display_path(resolve_safe_path("src/app.py", roots), roots)
    assert str(repo) not in shown and not shown.startswith(("/", "\\")) and ":" not in shown
