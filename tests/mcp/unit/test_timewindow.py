"""Time windows: explicit UTC offsets required, ordered, bounded."""

from datetime import datetime, timedelta, timezone

import pytest

from mcp_common.errors import InvalidArgument
from mcp_common.timewindow import format_utc, parse_utc, parse_window


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-08T10:31:02Z", datetime(2026, 10, 8, 10, 31, 2, tzinfo=timezone.utc)),
        ("2026-10-08T10:31:02.500z", datetime(2026, 10, 8, 10, 31, 2, 500000, tzinfo=timezone.utc)),
        ("2026-10-08T16:01:02+05:30", datetime(2026, 10, 8, 10, 31, 2, tzinfo=timezone.utc)),
    ],
)
def test_parse_utc_normalizes_to_utc(raw, expected):
    parsed = parse_utc(raw)
    assert parsed == expected
    assert parsed.utcoffset() == timedelta(0)


@pytest.mark.parametrize("raw", ["2026-10-08T10:31:02", "yesterday", "", "   "])
def test_parse_utc_rejects_naive_or_garbage(raw):
    with pytest.raises(InvalidArgument):
        parse_utc(raw)


def test_window_ok():
    window = parse_window("2026-10-08T10:00:00Z", "2026-10-08T11:00:00Z")
    assert window.end - window.start == timedelta(hours=1)
    assert window.contains(datetime(2026, 10, 8, 10, 30, tzinfo=timezone.utc))
    assert not window.contains(datetime(2026, 10, 8, 11, 0, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-08T11:00:00Z", "2026-10-08T10:00:00Z"),  # reversed
        ("2026-10-08T10:00:00Z", "2026-10-08T10:00:00Z"),  # empty
        ("2026-10-08T00:00:00Z", "2026-10-08T06:00:01Z"),  # over 6h default
    ],
)
def test_window_rejected(start, end):
    with pytest.raises(InvalidArgument):
        parse_window(start, end)


def test_window_max_is_configurable():
    parse_window("2026-10-08T00:00:00Z", "2026-10-08T12:00:00Z", max_window=timedelta(hours=12))
    with pytest.raises(InvalidArgument, match="1 hours"):
        parse_window("2026-10-08T00:00:00Z", "2026-10-08T02:00:00Z", max_window=timedelta(hours=1))


def test_format_utc():
    assert format_utc(datetime(2026, 10, 8, 10, 31, 2, tzinfo=timezone.utc)) == "2026-10-08T10:31:02.000Z"
    assert format_utc(datetime(2026, 10, 8, 10, 31, 2)) == "2026-10-08T10:31:02.000Z"  # naive taken as UTC
