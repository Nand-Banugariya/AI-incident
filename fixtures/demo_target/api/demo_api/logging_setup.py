"""File logging in the format the Linux Logs MCP parses.

Every entry starts with an ISO 8601 UTC timestamp; a stack trace follows on continuation lines that have
no timestamp, so the log reader can group it with the line that started it:

    2026-10-08T10:31:02.123Z ERROR demo_api.main [req=3f2a9c1e] Unhandled error on POST /refunds/42
    Traceback (most recent call last):
      ...
    TypeError: 'NoneType' object is not subscriptable
"""

import contextvars
import logging
import os
import time

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created)) + f".{int(record.msecs):03d}Z"


def configure_logging() -> None:
    path = os.environ.get("DEMO_LOG_FILE", "app.log")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(_UtcFormatter("%(asctime)s %(levelname)s %(name)s [req=%(request_id)s] %(message)s"))
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # uvicorn's own access log is noise for the investigation; our middleware logs each request instead
    logging.getLogger("uvicorn.access").disabled = True
