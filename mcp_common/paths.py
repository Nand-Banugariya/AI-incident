"""Path guard shared by every tool that touches the filesystem.

`resolve_safe_path()` is the ONLY way a caller-supplied path becomes a real path. It resolves symlinks and
Windows junctions before checking containment, so a link inside a root that points outside it is refused.
"""

import os
from pathlib import Path

from mcp_common.errors import PathNotAllowed


def resolve_roots(raw_roots: list[str]) -> list[Path]:
    """Resolves configured roots once at startup; a missing root is a configuration error, not a tool error."""
    if not raw_roots:
        raise ValueError("At least one allowed root must be configured")
    roots = []
    for raw in raw_roots:
        root = Path(raw).resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Allowed root is not a directory: {raw}")
        roots.append(root)
    return roots


def _has_stream_or_drive_colon(raw: str) -> bool:
    """On Windows, ':' after the drive spec means an alternate data stream ('file:stream') or a drive change."""
    if os.name != "nt":
        return False
    drive = Path(raw).drive
    rest = raw[len(drive):] if drive and not drive.startswith(("\\\\", "//")) else raw
    return ":" in rest


def resolve_safe_path(raw: str, roots: list[Path]) -> Path:
    """Resolves `raw` (relative to the first root, or absolute) and returns it only if it stays inside a root.

    The path does not have to exist: a non-existent path inside a root is returned, and the tool reports
    `not_found` when it opens it. Raises PathNotAllowed otherwise.
    """
    if not roots:
        raise PathNotAllowed("No allowed roots are configured")
    if not isinstance(raw, str) or "\x00" in raw or _has_stream_or_drive_colon(raw):
        raise PathNotAllowed()

    candidate = Path(raw)
    # Joining a drive-relative ('D:foo') or rooted ('\\foo') path onto a root silently leaves the root,
    # which the containment check below then catches
    path = (candidate if candidate.is_absolute() else roots[0] / candidate).resolve(strict=False)

    # WindowsPath comparisons are case-insensitive, so 'C:\\Repo' and 'c:\\repo' are the same root
    if not any(path == root or path.is_relative_to(root) for root in roots):
        raise PathNotAllowed()
    return path


def display_path(path: Path, roots: list[Path]) -> str:
    """The path as callers should see it: relative to its root, with '/' separators, never the host path."""
    for root in roots:
        if path == root or path.is_relative_to(root):
            return path.relative_to(root).as_posix() or "."
    raise PathNotAllowed()
