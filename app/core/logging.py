"""Structured JSON logging (§9): structlog for new code, stdlib `logging` records rendered the same way."""

import logging
import sys
from typing import TextIO

import structlog

from app.core.config import settings


def configure_logging(stream: TextIO | None = None) -> None:
    """Routes structlog and stdlib logging (including uvicorn/celery) through one JSON pipeline.

    Context bound with `structlog.contextvars.bind_contextvars(incident_id=..., ...)` is attached
    to every line, so logs can be queried per incident. `stream` defaults to stdout.
    """
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer() if settings.LOG_FORMAT == "console" else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL.upper())

    # Let uvicorn's loggers flow through the root handler instead of their own plain-text ones
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
