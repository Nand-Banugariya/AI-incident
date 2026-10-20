"""The Local Code MCP server: six read-only tools over one repository root.

Every tool goes through `@safe_tool` (envelope, timeout, limits) and every path through the repo's path guard.
"""

import re
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from mcp_common.capabilities import describe
from mcp_common.envelope import ToolResult, safe_tool
from mcp_common.errors import InvalidArgument, NotFound, ResultTooLarge
from mcp_common.limits import CODE_SEARCH_TIMEOUT_S, DEFAULT_TIMEOUT_S, MAX_ROWS
from mcp_servers.local_code import SERVER_NAME, VERSION
from mcp_servers.local_code.methods import find_method
from mcp_servers.local_code.repo import Repo
from mcp_servers.local_code.schemas import (
    Capabilities,
    CodeMatch,
    FileContent,
    FileEntry,
    Line,
    MethodBody,
    Reference,
)
from mcp_servers.local_code.search import Searcher, glob_matches
from mcp_servers.local_code.settings import LocalCodeSettings

_SYMBOL = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$")
_SEARCH_BUDGET_S = CODE_SEARCH_TIMEOUT_S - 5  # the engine stops itself before @safe_tool's hard timeout


def _bounded(value: int | None, default: int, label: str) -> int:
    if value is None:
        return default
    if not 1 <= value <= MAX_ROWS:
        raise InvalidArgument(f"'{label}' must be between 1 and {MAX_ROWS}")
    return value


def build_server(settings: LocalCodeSettings) -> MCPServer:
    repo = Repo(settings.repo_root, settings.max_file_bytes)
    searcher = Searcher(repo, use_ripgrep=settings.use_ripgrep)
    server = MCPServer(
        SERVER_NAME,
        version=VERSION,
        instructions="Read-only access to one source repository. Paths are relative to the repository root.",
    )

    def tool(timeout_s: float = DEFAULT_TIMEOUT_S):
        return safe_tool(source=settings.source, timeout_s=timeout_s, meta=repo.head_meta)

    def text_file(raw: str) -> Path:
        path = repo.resolve(raw)
        if not path.exists():
            raise NotFound(f"'{raw}' does not exist")
        if not path.is_file():
            raise InvalidArgument(f"'{raw}' is not a file")
        if path.stat().st_size > settings.max_file_bytes:
            raise ResultTooLarge(f"'{raw}' is larger than {settings.max_file_bytes} bytes")
        if not repo.readable_text(path):
            raise InvalidArgument(f"'{raw}' is a binary file")
        return path

    @server.tool()
    @tool()
    def list_files(dir: str = ".", glob: str | None = None) -> list[FileEntry]:
        """List files under `dir` (relative to the repo root). `glob` filters by file name ('*.py') or by
        relative path when it contains '/' ('src/**/*.cs'). Files excluded by .gitignore are not listed."""
        base = repo.resolve(dir)
        if not base.is_dir():
            raise NotFound(f"'{dir}' is not a directory")
        entries = []
        for path in repo.iter_files(base):
            relative = repo.display(path)
            if glob_matches(relative, glob):
                entries.append(FileEntry(path=relative, size=path.stat().st_size))
        return sorted(entries, key=lambda entry: entry.path)

    @server.tool()
    @tool()
    def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> FileContent:
        """Read a text file, optionally a 1-based inclusive line range. Long files are truncated to the
        result limit; use start_line/end_line to page through them."""
        file = text_file(path)
        start = 1 if start_line is None else start_line
        if start < 1 or (end_line is not None and end_line < start):
            raise InvalidArgument("Line range must satisfy 1 <= start_line <= end_line")
        lines: list[Line] = []
        total = 0
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            for number, text in enumerate(handle, start=1):
                total = number
                if number >= start and (end_line is None or number <= end_line):
                    lines.append(Line(n=number, text=text.rstrip("\r\n")))
        if total and start > total:
            raise InvalidArgument(f"start_line {start} is past the end of the file ({total} lines)")
        return FileContent(path=repo.display(file), start_line=start, end_line=lines[-1].n if lines else start,
                           total_lines=total, lines=lines)

    @server.tool()
    @tool(timeout_s=CODE_SEARCH_TIMEOUT_S)
    def search_code(pattern: str, glob: str | None = None, max_results: int | None = None,
                    fixed_strings: bool = False, case_sensitive: bool = True) -> ToolResult:
        """Search file contents with a regular expression (or a literal with fixed_strings=true).
        Respects .gitignore and skips binary files. Returns at most max_results matches (default 50)."""
        limit = _bounded(max_results, 50, "max_results")
        hits = searcher.search(pattern, glob=glob, max_results=limit, fixed_strings=fixed_strings,
                               case_sensitive=case_sensitive, timeout_s=_SEARCH_BUDGET_S)
        data = [CodeMatch(path=h.path, line=h.line, text=h.text) for h in hits]
        return ToolResult(data, engine=searcher.engine, limit_reached=len(data) >= limit)

    @server.tool()
    @tool()
    def get_method(file: str, name: str) -> ToolResult:
        """Return the full definition of a function, method or class in `file`. `name` may be qualified
        ('OrderService.search') when the plain name is ambiguous."""
        path = text_file(file)
        source = path.read_text(encoding="utf-8", errors="replace")
        match, parser = find_method(source, name, path.suffix)
        body = "\n".join(source.splitlines()[match.start_line - 1:match.end_line])
        data = MethodBody(file=repo.display(path), name=match.name, start_line=match.start_line,
                          end_line=match.end_line, body=body)
        return ToolResult(data, parser=parser)

    @server.tool()
    @tool(timeout_s=CODE_SEARCH_TIMEOUT_S)
    def find_references(symbol: str, glob: str | None = None, max_results: int | None = None) -> ToolResult:
        """Find textual (whole-word) occurrences of `symbol`. This is text search, NOT semantic analysis:
        treat the results as possible references, including the definition itself."""
        if not symbol or len(symbol) > 200 or not _SYMBOL.match(symbol):
            raise InvalidArgument("'symbol' must be an identifier, optionally dotted")
        limit = _bounded(max_results, 100, "max_results")
        hits = searcher.search(symbol, glob=glob, max_results=limit, fixed_strings=True, word=True,
                               timeout_s=_SEARCH_BUDGET_S)
        data = [Reference(path=h.path, line=h.line, text=h.text) for h in hits]
        return ToolResult(data, match="textual", engine=searcher.engine, limit_reached=len(data) >= limit)

    @server.tool()
    @tool()
    def describe_capabilities() -> Capabilities:
        """What this server can provide. Cheap; also used as the health probe."""
        return Capabilities(**describe(SERVER_NAME, ["source_code"], VERSION))

    return server
