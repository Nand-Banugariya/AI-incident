"""cap_result: rows and bytes budgets, dict results trimmed through their largest list, long strings clipped."""

from datetime import datetime, timezone
from pathlib import PurePosixPath

import pytest

from mcp_common.errors import ResultTooLarge
from mcp_common.limits import MAX_BYTES, MAX_ROWS, cap_result, json_size


def test_small_list_untouched():
    capped = cap_result([1, 2, 3])
    assert capped.data == [1, 2, 3]
    assert capped.count == 3
    assert capped.truncated is False and capped.hint is None


def test_rows_capped_at_max_rows():
    capped = cap_result(list(range(MAX_ROWS + 50)))
    assert len(capped.data) == MAX_ROWS
    assert capped.count == MAX_ROWS + 50
    assert capped.truncated is True
    assert f"{MAX_ROWS} of {MAX_ROWS + 50}" in capped.hint


def test_bytes_capped_and_result_fits():
    capped = cap_result([{"text": "y" * 2000} for _ in range(100)])
    assert capped.truncated is True
    assert json_size(capped.data) <= MAX_BYTES
    assert 0 < len(capped.data) < 100


def test_dict_trimmed_through_largest_list_keeping_other_fields():
    data = {"path": "a.py", "total_lines": 5000, "lines": [{"n": i, "text": "z" * 200} for i in range(5000)]}
    capped = cap_result(data)
    assert capped.data["path"] == "a.py" and capped.data["total_lines"] == 5000
    assert len(capped.data["lines"]) <= MAX_ROWS
    assert capped.count == 5000
    assert capped.truncated is True
    assert json_size(capped.data) <= MAX_BYTES


def test_long_strings_clipped_in_place():
    capped = cap_result([{"text": "q" * 20_000}], max_string_chars=100)
    assert capped.truncated is True
    assert capped.data[0]["text"].startswith("q" * 100)
    assert "truncated 19900 chars" in capped.data[0]["text"]


def test_non_json_types_are_normalized():
    moment = datetime(2026, 10, 8, 10, 31, 2, tzinfo=timezone.utc)
    capped = cap_result({"ts": moment, "path": PurePosixPath("src/a.py")})
    assert capped.data == {"ts": "2026-10-08T10:31:02Z", "path": "src/a.py"}


def test_single_oversized_row_raises():
    with pytest.raises(ResultTooLarge):
        cap_result([{f"k{i}": "x" * 7000 for i in range(20)}])


def test_oversized_scalar_dict_raises():
    with pytest.raises(ResultTooLarge):
        cap_result({f"k{i}": "x" * 7000 for i in range(20)})
