"""Find a method/function definition in a source file: `get_method(file, name)`.

Python uses the standard-library `ast` module, which gives exact line ranges (`parser: "python-ast"`).
Brace languages (C#, Java, JS/TS, Go, C/C++, ...) use a regex scanner that matches parentheses and braces
while skipping strings and comments (`parser: "regex"`); good for well-formed code, not a real parser.
A tree-sitter parser can replace the regex one later without changing the tool's output.

`name` may be qualified: `ClassName.method` narrows the search to that class.
"""

import ast
import re
from dataclasses import dataclass

from mcp_common.errors import InvalidArgument, NotFound

PYTHON_EXTENSIONS = frozenset({".py", ".pyi"})
BRACE_EXTENSIONS = frozenset({
    ".cs", ".java", ".kt", ".kts", ".scala", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".c", ".h",
    ".cc", ".cpp", ".cxx", ".hpp", ".rs", ".php", ".swift", ".dart",
})
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$")


@dataclass(frozen=True)
class MethodMatch:
    name: str  # qualified where the enclosing class is known
    start_line: int  # includes decorators / attributes
    end_line: int


def find_method(source: str, name: str, suffix: str) -> tuple[MethodMatch, str]:
    """Returns (match, parser). Raises NotFound, or InvalidArgument when the name is ambiguous or unsupported."""
    if not name or len(name) > 200 or not _IDENTIFIER.match(name):
        raise InvalidArgument("'name' must be an identifier, optionally qualified (e.g. OrderService.Search)")
    suffix = suffix.lower()
    if suffix in PYTHON_EXTENSIONS:
        try:
            return _pick(find_python(source, name), name), "python-ast"
        except SyntaxError:
            pass  # unparsable file (e.g. Python 2): fall through to the indentation-based regex
        return _pick(find_python_regex(source, name), name), "regex"
    if suffix in BRACE_EXTENSIONS:
        return _pick(find_brace(source, name), name), "regex"
    raise InvalidArgument(f"get_method does not support '{suffix or 'extensionless'}' files")


def _pick(matches: list[MethodMatch], name: str) -> MethodMatch:
    if not matches:
        raise NotFound(f"No definition of '{name}' found in this file")
    if len(matches) > 1:
        options = ", ".join(f"{m.name} (line {m.start_line})" for m in matches[:10])
        raise InvalidArgument(f"'{name}' is ambiguous in this file; qualify it. Candidates: {options}")
    return matches[0]


def _name_matches(qualified: str, simple: str, wanted: str) -> bool:
    return wanted == simple if "." not in wanted else qualified == wanted or qualified.endswith("." + wanted)


# --- Python -------------------------------------------------------------------------------------------

def find_python(source: str, name: str) -> list[MethodMatch]:
    tree = ast.parse(source)
    matches: list[MethodMatch] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = f"{prefix}{child.name}"
                if _name_matches(qualified, child.name, name):
                    start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                    matches.append(MethodMatch(qualified, start, child.end_lineno or child.lineno))
                visit(child, qualified + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return matches


def find_python_regex(source: str, name: str) -> list[MethodMatch]:
    simple = name.rsplit(".", 1)[-1]
    lines = source.splitlines()
    pattern = re.compile(rf"^(\s*)(?:async\s+)?(?:def|class)\s+{re.escape(simple)}\b")
    matches = []
    for index, line in enumerate(lines):
        found = pattern.match(line)
        if not found:
            continue
        indent = len(found.group(1))
        end = index
        for later in range(index + 1, len(lines)):
            text = lines[later]
            if text.strip() and len(text) - len(text.lstrip()) <= indent:
                break
            if text.strip():
                end = later
        matches.append(MethodMatch(simple, index + 1, end + 1))
    return matches


# --- Brace languages ----------------------------------------------------------------------------------

def _skip_literal_or_comment(text: str, i: int) -> int:
    """If a string/char literal or comment starts at i, returns the index just past it; otherwise i."""
    ch = text[i]
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if ch == "/" and nxt == "/":
        end = text.find("\n", i)
        return len(text) if end == -1 else end
    if ch == "/" and nxt == "*":
        end = text.find("*/", i + 2)
        return len(text) if end == -1 else end + 2
    if ch == "@" and nxt == '"':  # C# verbatim string: "" is an escaped quote
        j = i + 2
        while j < len(text):
            if text[j] == '"':
                if j + 1 < len(text) and text[j + 1] == '"':
                    j += 2
                    continue
                return j + 1
            j += 1
        return len(text)
    if ch in "\"'`":
        j = i + 1
        while j < len(text):
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == ch:
                return j + 1
            if text[j] == "\n" and ch != "`":
                return j  # unterminated (or a lone apostrophe in a char-less language): stop at end of line
            j += 1
        return len(text)
    return i


def _match_close(text: str, open_index: int, opener: str, closer: str) -> int | None:
    """Index of the bracket closing the one at open_index, skipping literals and comments."""
    depth = 0
    i = open_index
    while i < len(text):
        skipped = _skip_literal_or_comment(text, i)
        if skipped != i:
            i = skipped
            continue
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


_TYPE_DECL = re.compile(r"\b(?:class|struct|interface|record|enum|object|impl|trait)\s+([A-Za-z_$][\w$]*)")
# Text allowed before a method name on its line: start of line, or modifiers / a return type / `func (r *T)`
_DECL_PREFIX = re.compile(r"(?:^|[\w>\]?*)])\s*$")
_NOT_A_DECL = re.compile(r"(?:\b(?:new|return|await|throw|yield|else|case)\s*$)|[.=,(!&|+\-/%:]\s*$")


def _enclosing_types(text: str) -> list[tuple[str, int, int]]:
    spans = []
    for found in _TYPE_DECL.finditer(text):
        brace = text.find("{", found.end())
        semicolon = text.find(";", found.end())
        if brace == -1 or (semicolon != -1 and semicolon < brace):
            continue
        close = _match_close(text, brace, "{", "}")
        if close is not None:
            spans.append((found.group(1), brace, close))
    return spans


def find_brace(source: str, name: str) -> list[MethodMatch]:
    simple = name.rsplit(".", 1)[-1]
    wanted_type = name.rsplit(".", 2)[-2] if "." in name else None
    text = source
    types = _enclosing_types(text)
    matches: list[MethodMatch] = []

    for found in re.finditer(rf"(?<![\w$.]){re.escape(simple)}\s*(?:<[^<>(){{}};]*>)?\s*\(", text):
        line_start = text.rfind("\n", 0, found.start()) + 1
        prefix = text[line_start:found.start()]
        if not _DECL_PREFIX.search(prefix) or _NOT_A_DECL.search(prefix) or prefix.lstrip().startswith(("//", "*")):
            continue
        close_paren = _match_close(text, found.end() - 1, "(", ")")
        if close_paren is None:
            continue

        # After the parameters: return types, `throws`, `where T :`, base-constructor calls... until { ; or =>
        j = close_paren + 1
        body_start = None
        while j < len(text):
            skipped = _skip_literal_or_comment(text, j)
            if skipped != j:
                j = skipped
                continue
            if text[j] == "{":
                body_start = j
                break
            if text[j] == ";" or text.startswith("=>", j):
                break
            if text[j] == "(":  # e.g. `: base(x)` in a C# constructor
                inner = _match_close(text, j, "(", ")")
                if inner is None:
                    break
                j = inner + 1
                continue
            j += 1
        if j >= len(text):
            continue

        if body_start is not None:
            end_index = _match_close(text, body_start, "{", "}")
            if end_index is None:
                continue
        else:
            # Abstract/interface declaration (`;`) or expression body (`=> expr;`): ends at the next `;`
            semicolon = text.find(";", j)
            end_index = semicolon if semicolon != -1 else j

        owner = next((t for t, open_i, close_i in reversed(types) if open_i < found.start() < close_i), None)
        if wanted_type and owner != wanted_type:
            continue

        start_line = _line_of(text, found.start())
        lines = text.splitlines()
        while start_line > 1 and lines[start_line - 2].strip().startswith(("[", "@")):  # attributes / annotations
            start_line -= 1
        qualified = f"{owner}.{simple}" if owner else simple
        matches.append(MethodMatch(qualified, start_line, _line_of(text, end_index)))
    return matches
