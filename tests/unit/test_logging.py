"""Structured logging: JSON output, stdlib records included, and per-incident context."""

import io
import json
import logging

import pytest
import structlog

from app.core import logging as app_logging
from app.core.config import settings


@pytest.fixture
def configured(monkeypatch):
    """Installs the JSON pipeline (writing to a StringIO) on the root logger, then restores the previous logging state."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    monkeypatch.setattr(settings, "LOG_FORMAT", "json")
    monkeypatch.setattr(settings, "LOG_LEVEL", "INFO")
    stream = io.StringIO()
    app_logging.configure_logging(stream)
    yield stream
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    root.handlers, root.level = saved_handlers, saved_level


def _lines(stream: io.StringIO) -> list[dict]:
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    stream.seek(0)
    stream.truncate()
    return lines


def test_stdlib_logging_is_rendered_as_json(configured):
    logging.getLogger("app.example").info("hello %s", "world")
    [entry] = _lines(configured)
    assert entry["event"] == "hello world"
    assert entry["level"] == "info"
    assert entry["logger"] == "app.example"
    assert entry["timestamp"].endswith("Z")


def test_structlog_loggers_use_the_same_pipeline(configured):
    structlog.get_logger("app.native").info("did_thing", count=3)
    [entry] = _lines(configured)
    assert entry["event"] == "did_thing"
    assert entry["count"] == 3


def test_bound_incident_context_is_attached_to_every_line(configured):
    structlog.contextvars.bind_contextvars(incident_id="inc-1", run_id="run-1", node_name="planning")
    logging.getLogger("app.example").warning("from stdlib")
    structlog.get_logger("app.native").info("from structlog")

    for entry in _lines(configured):
        assert (entry["incident_id"], entry["run_id"], entry["node_name"]) == ("inc-1", "run-1", "planning")


def test_cleared_context_does_not_leak(configured):
    structlog.contextvars.bind_contextvars(incident_id="inc-1")
    structlog.contextvars.clear_contextvars()
    logging.getLogger("app.example").info("after clear")
    [entry] = _lines(configured)
    assert "incident_id" not in entry


def test_exceptions_are_serialised(configured):
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("app.example").exception("failed")
    [entry] = _lines(configured)
    assert entry["event"] == "failed"
    assert "ValueError: boom" in entry["exception"]


def test_level_filtering(configured):
    logging.getLogger("app.example").debug("hidden")
    assert _lines(configured) == []
