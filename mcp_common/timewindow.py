"""Time windows for windowed tools (logs, events). Times are ISO 8601 with an explicit UTC offset.

A naive timestamp is refused rather than guessed: an investigation window off by a timezone silently misses
the incident.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mcp_common.errors import InvalidArgument

DEFAULT_MAX_WINDOW = timedelta(hours=6)


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end


def parse_utc(value: str, field: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise InvalidArgument(f"'{field}' is required (ISO 8601 UTC, e.g. 2026-10-08T10:31:02Z)")
    text = value.strip()
    if text.endswith(("z", "Z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise InvalidArgument(f"'{field}' is not an ISO 8601 timestamp") from None
    if parsed.tzinfo is None:
        raise InvalidArgument(f"'{field}' must include a UTC offset (e.g. a trailing 'Z')")
    return parsed.astimezone(timezone.utc)


def format_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_window(start: str, end: str, *, max_window: timedelta = DEFAULT_MAX_WINDOW) -> TimeWindow:
    window = TimeWindow(parse_utc(start, "start"), parse_utc(end, "end"))
    if window.end <= window.start:
        raise InvalidArgument("'end' must be after 'start'")
    if window.end - window.start > max_window:
        hours = max_window.total_seconds() / 3600
        raise InvalidArgument(f"Time window exceeds the maximum of {hours:g} hours; request a shorter window")
    return window
