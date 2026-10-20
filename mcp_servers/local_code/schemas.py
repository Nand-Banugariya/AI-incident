"""Output contracts of the Local Code tools (the `data` field of the envelope).

The contract test validates every tool's wire output against these, and the registry can do the same.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileEntry(_Strict):
    path: str
    size: int


class Line(_Strict):
    n: int
    text: str


class FileContent(_Strict):
    path: str
    start_line: int
    end_line: int
    total_lines: int
    lines: list[Line]


class CodeMatch(_Strict):
    path: str
    line: int
    text: str


class Reference(CodeMatch):
    match: Literal["textual"] = "textual"


class MethodBody(_Strict):
    file: str
    name: str
    start_line: int
    end_line: int
    body: str


class Capabilities(_Strict):
    server: str
    capabilities: list[str]
    version: str


# tool name -> model of `data` (list[...] tools validate each item)
TOOL_OUTPUTS: dict[str, tuple[type[BaseModel], bool]] = {
    "list_files": (FileEntry, True),
    "read_file": (FileContent, False),
    "search_code": (CodeMatch, True),
    "get_method": (MethodBody, False),
    "find_references": (Reference, True),
    "describe_capabilities": (Capabilities, False),
}
