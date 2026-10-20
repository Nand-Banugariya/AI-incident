"""Repository access: file enumeration that respects .gitignore, binary detection, and git HEAD state.

Git is run with repository hooks for code execution switched off (`core.fsmonitor=false`), never with a
write command, and with optional locks disabled so a read never touches the index.
"""

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator

from mcp_common.errors import PathNotAllowed
from mcp_common.paths import resolve_safe_path

logger = logging.getLogger(__name__)

# Skipped when the root is not a git checkout (inside one, .gitignore decides)
DEFAULT_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    "bin", "obj", "dist", "build", ".idea", ".vs", ".vscode",
})
_BINARY_SNIFF_BYTES = 8192
_GIT_TIMEOUT_S = 10
_HEAD_CACHE_TTL_S = 5.0


def _git(root: Path, *args: str, timeout: float = _GIT_TIMEOUT_S) -> subprocess.CompletedProcess[bytes] | None:
    command = [
        "git", "-C", str(root),
        "-c", "core.fsmonitor=false",
        "-c", f"safe.directory={root.as_posix()}",
        *args,
    ]
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("git %s failed: %s", args[0], type(exc).__name__)
        return None
    return result if result.returncode == 0 else None


def is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return True


class Repo:
    def __init__(self, root: Path, max_file_bytes: int):
        self.root = root
        self.roots = [root]
        self.max_file_bytes = max_file_bytes
        self.is_git = _git(root, "rev-parse", "--is-inside-work-tree") is not None
        self._head_lock = threading.Lock()
        self._head_cache: tuple[float, dict] | None = None

    # -- HEAD state (meta on every call) -----------------------------------------------------------

    def head_meta(self) -> dict:
        """`repo_head` and `dirty`, so a mismatch with the deployed commit is visible on every result."""
        with self._head_lock:
            now = time.monotonic()
            if self._head_cache and now - self._head_cache[0] < _HEAD_CACHE_TTL_S:
                return self._head_cache[1]
            meta = {"repo_head": None, "dirty": None}
            if self.is_git:
                head = _git(self.root, "rev-parse", "HEAD")
                status = _git(self.root, "status", "--porcelain", "--untracked-files=no")
                if head is not None:
                    meta["repo_head"] = head.stdout.decode().strip()
                if status is not None:
                    meta["dirty"] = bool(status.stdout.strip())
            self._head_cache = (now, meta)
            return meta

    # -- Paths -------------------------------------------------------------------------------------

    def resolve(self, raw: str) -> Path:
        return resolve_safe_path(raw, self.roots)

    def display(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or "."

    # -- Enumeration -------------------------------------------------------------------------------

    def _candidates(self, base: Path) -> Iterator[Path]:
        if self.is_git:
            listed = _git(base, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
            if listed is not None:
                for raw in listed.stdout.split(b"\x00"):
                    if raw:
                        yield base / raw.decode("utf-8", errors="surrogateescape")
                return
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_SKIP_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name

    def iter_files(self, base: Path | None = None) -> Iterator[Path]:
        """Every regular file under `base` that .gitignore doesn't exclude and that resolves inside the root.

        Each candidate goes through the path guard again: a symlink or junction inside the repo that points
        outside it is dropped here, whatever the enumeration method listed.
        """
        for candidate in self._candidates(base or self.root):
            try:
                path = resolve_safe_path(str(candidate), self.roots)
            except PathNotAllowed:
                continue
            if path.is_file():
                yield path

    def readable_text(self, path: Path) -> bool:
        try:
            return path.stat().st_size <= self.max_file_bytes and not is_binary(path)
        except OSError:
            return False
