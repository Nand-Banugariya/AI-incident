"""Local Code server settings, read from the environment at startup.

One server instance serves ONE repository root, so `meta.repo_head` is unambiguous. To expose two
repositories, run two instances (one per data source).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from mcp_common.paths import resolve_roots


@dataclass(frozen=True)
class LocalCodeSettings:
    repo_root: Path
    source: str = "local-code"  # the data source name reported in meta.source
    max_file_bytes: int = 2_000_000  # larger files are skipped by search and refused by read_file
    use_ripgrep: bool = True  # used when `rg` is on PATH; the Python engine is the fallback

    @classmethod
    def from_env(cls) -> "LocalCodeSettings":
        raw_root = os.environ.get("LOCAL_CODE_REPO_ROOT", "")
        if not raw_root:
            raise ValueError("LOCAL_CODE_REPO_ROOT is not set")
        return cls(
            repo_root=resolve_roots([raw_root])[0],
            source=os.environ.get("DATA_SOURCE_NAME", "local-code"),
            max_file_bytes=int(os.environ.get("LOCAL_CODE_MAX_FILE_BYTES", "2000000")),
            use_ripgrep=os.environ.get("LOCAL_CODE_USE_RIPGREP", "true").lower() in ("1", "true", "yes"),
        )

    @classmethod
    def for_root(cls, root: str | Path, **overrides) -> "LocalCodeSettings":
        return cls(repo_root=resolve_roots([str(root)])[0], **overrides)
