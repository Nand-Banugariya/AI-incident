"""Per-result size limits. Every tool result passes through `cap_result()` before it leaves the server.

Over the limit, the result is trimmed (never rejected) and the caller is told how to narrow the query.
Only a result that cannot be trimmed below the budget, such as one enormous row, becomes `result_too_large`.
"""

import json
from dataclasses import dataclass
from typing import Any

from pydantic_core import to_jsonable_python

from mcp_common.errors import ResultTooLarge

MAX_ROWS = 200
MAX_BYTES = 64_000
MAX_STRING_CHARS = 8_000  # one log line or code line; longer values are clipped in place

DEFAULT_TIMEOUT_S = 10.0  # log and state queries
CODE_SEARCH_TIMEOUT_S = 30.0

NARROW_HINT = "narrow the time window, add a pattern, or lower the limit"


@dataclass(frozen=True)
class CappedResult:
    data: Any
    count: int | None  # items BEFORE trimming, when the result is a collection
    truncated: bool
    hint: str | None


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _clip_strings(value: Any, max_chars: int) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value, False
        return f"{value[:max_chars]}...[truncated {len(value) - max_chars} chars]", True
    if isinstance(value, list):
        clipped = [_clip_strings(item, max_chars) for item in value]
        return [item for item, _ in clipped], any(flag for _, flag in clipped)
    if isinstance(value, dict):
        clipped = {key: _clip_strings(item, max_chars) for key, item in value.items()}
        return {key: item for key, (item, _) in clipped.items()}, any(flag for _, flag in clipped.values())
    return value, False


def _fit_list(items: list[Any], budget: int, wrap) -> list[Any]:
    """Longest prefix of `items` whose wrapped form fits in `budget` bytes (binary search)."""
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if json_size(wrap(items[:mid])) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return items[:lo]


def cap_result(
    data: Any,
    *,
    max_rows: int = MAX_ROWS,
    max_bytes: int = MAX_BYTES,
    max_string_chars: int = MAX_STRING_CHARS,
) -> CappedResult:
    """Trims `data` to the row and byte budgets.

    A list is trimmed by rows. A dict is trimmed through its largest list field (e.g. `lines` of `read_file`),
    keeping its other fields intact. Over-long strings are clipped wherever they appear.
    """
    data = to_jsonable_python(data, fallback=str)
    data, truncated = _clip_strings(data, max_string_chars)

    rows_key: str | None = None
    if isinstance(data, list):
        rows = data
        wrap = lambda part: part  # noqa: E731
    elif isinstance(data, dict):
        list_fields = [key for key, value in data.items() if isinstance(value, list)]
        rows_key = max(list_fields, key=lambda key: len(data[key]), default=None)
        rows = data[rows_key] if rows_key else None
        wrap = lambda part: {**data, rows_key: part}  # noqa: E731
    else:
        rows = None

    if rows is None:
        if json_size(data) > max_bytes:
            raise ResultTooLarge(f"Result exceeds {max_bytes} bytes and cannot be trimmed; {NARROW_HINT}")
        return CappedResult(data=data, count=None, truncated=truncated, hint=None)

    count = len(rows)
    kept = rows[:max_rows]
    if json_size(wrap(kept)) > max_bytes:
        kept = _fit_list(kept, max_bytes, wrap)
        if not kept and rows:
            raise ResultTooLarge(f"A single item exceeds {max_bytes} bytes; {NARROW_HINT}")

    hint = None
    if len(kept) < count:
        truncated = True
        hint = f"Returned {len(kept)} of {count} items; {NARROW_HINT}"
    return CappedResult(data=wrap(kept), count=count, truncated=truncated, hint=hint)
