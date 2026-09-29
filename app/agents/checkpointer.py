"""LangGraph checkpointer factory — Postgres-backed so investigations survive restarts (§6)."""

import logging
from contextlib import contextmanager
from typing import Iterator
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver

from app.core.config import settings

logger = logging.getLogger(__name__)

_postgres_setup_done = False
_memory_saver = InMemorySaver()


def _postgres_conn_string(db_url: str) -> str | None:
    """Converts a SQLAlchemy URL (any postgres driver) to a plain libpq URL, or None if not Postgres."""
    scheme, sep, rest = db_url.partition("://")
    if sep and scheme.split("+", 1)[0] in ("postgresql", "postgres"):
        return f"postgresql://{rest}"
    return None


@contextmanager
def get_checkpointer() -> Iterator[BaseCheckpointSaver]:
    """Yields a checkpointer bound to the platform DB for the duration of one graph run."""
    global _postgres_setup_done

    conn_string = _postgres_conn_string(settings.APP_DB_URL)
    if conn_string is None:
        logger.warning(
            "APP_DB_URL is not PostgreSQL; using the in-memory LangGraph checkpointer. "
            "Investigations will NOT survive a worker restart — local development only."
        )
        yield _memory_saver
        return

    with PostgresSaver.from_conn_string(conn_string) as saver:
        if not _postgres_setup_done:
            saver.setup()  # idempotent: creates/migrates checkpoint tables
            _postgres_setup_done = True
        yield saver
