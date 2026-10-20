"""Text search over the repository: ripgrep when installed, a pure-Python engine otherwise.

Both engines return the same shape and both drop any match whose file resolves outside the root.
"""

import fnmatch
import json
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mcp_common.errors import InvalidArgument, PathNotAllowed
from mcp_servers.local_code.repo import Repo

MAX_PATTERN_CHARS = 1000
MAX_LINE_CHARS = 500  # matched lines are clipped; a minified file can have one enormous line


@dataclass(frozen=True)
class SearchHit:
    path: str
    line: int
    text: str


def glob_matches(relative_posix: str, glob: str | None) -> bool:
    """A glob without '/' matches the file name ('*.py'); one with '/' matches the whole relative path."""
    if not glob:
        return True
    if "/" in glob:
        return fnmatch.fnmatchcase(relative_posix, glob) or PurePosixPath(relative_posix).match(glob)
    return fnmatch.fnmatchcase(PurePosixPath(relative_posix).name, glob)


def compile_pattern(pattern: str, *, fixed_strings: bool, case_sensitive: bool, word: bool = False) -> re.Pattern:
    if not pattern:
        raise InvalidArgument("'pattern' must not be empty")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise InvalidArgument(f"'pattern' is longer than {MAX_PATTERN_CHARS} characters")
    source = re.escape(pattern) if fixed_strings else pattern
    if word:
        source = rf"\b{source}\b"
    try:
        return re.compile(source, 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise InvalidArgument(f"'pattern' is not a valid regular expression: {exc.msg}") from None


def _clip(text: str) -> str:
    text = text.rstrip("\r\n")
    return text if len(text) <= MAX_LINE_CHARS else text[:MAX_LINE_CHARS] + "..."


class Searcher:
    def __init__(self, repo: Repo, *, use_ripgrep: bool = True):
        self.repo = repo
        self.rg = shutil.which("rg") if use_ripgrep else None

    @property
    def engine(self) -> str:
        return "ripgrep" if self.rg else "python"

    def search(
        self,
        pattern: str,
        *,
        glob: str | None,
        max_results: int,
        fixed_strings: bool = False,
        case_sensitive: bool = True,
        word: bool = False,
        timeout_s: float = 25.0,
    ) -> list[SearchHit]:
        compiled = compile_pattern(pattern, fixed_strings=fixed_strings, case_sensitive=case_sensitive, word=word)
        if self.rg:
            return self._search_rg(compiled, glob=glob, max_results=max_results, timeout_s=timeout_s)
        return self._search_python(compiled, glob=glob, max_results=max_results, timeout_s=timeout_s)

    def _search_python(self, compiled: re.Pattern, *, glob: str | None, max_results: int,
                       timeout_s: float) -> list[SearchHit]:
        deadline = time.monotonic() + timeout_s
        hits: list[SearchHit] = []
        for path in self.repo.iter_files():
            if time.monotonic() > deadline:
                raise TimeoutError
            relative = self.repo.display(path)
            if not glob_matches(relative, glob) or not self.repo.readable_text(path):
                continue
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, start=1):
                    if compiled.search(line):
                        hits.append(SearchHit(relative, number, _clip(line)))
                        if len(hits) >= max_results:
                            return hits
        return hits

    def _search_rg(self, compiled: re.Pattern, *, glob: str | None, max_results: int,
                   timeout_s: float) -> list[SearchHit]:
        # The Python pattern is passed through: plain regex syntax is shared by both engines
        command = [
            self.rg, "--json", "--no-config", "--no-follow",
            "--max-filesize", str(self.repo.max_file_bytes),
            "--max-columns", str(MAX_LINE_CHARS * 4),
        ]
        if compiled.flags & re.IGNORECASE:
            command.append("--ignore-case")
        if glob:
            command += ["--glob", glob if "/" in glob else f"**/{glob}"]
        command += ["-e", compiled.pattern, "--", "."]

        hits: list[SearchHit] = []
        process = subprocess.Popen(command, cwd=self.repo.root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL)
        timed_out = threading.Event()

        def kill_on_timeout() -> None:
            timed_out.set()
            process.kill()

        watchdog = threading.Timer(timeout_s, kill_on_timeout)
        watchdog.start()
        try:
            for raw in process.stdout:
                event = json.loads(raw)
                if event.get("type") != "match":
                    continue
                data = event["data"]
                text = data["path"].get("text")
                if text is None:
                    continue  # non-UTF-8 path
                try:
                    path = self.repo.resolve(str(self.repo.root / text))
                except PathNotAllowed:
                    continue
                line_text = (data.get("lines") or {}).get("text", "")
                hits.append(SearchHit(self.repo.display(path), data["line_number"], _clip(line_text)))
                if len(hits) >= max_results:
                    break
        finally:
            watchdog.cancel()
            process.kill()
            process.wait()
        if timed_out.is_set():
            raise TimeoutError
        return hits
