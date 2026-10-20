"""Connection pool. Each request tags its connection with the request id via application_name, so the
Postgres log line (prefix 'app=%a') can be correlated with the API log line for the same request."""

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg_pool import ConnectionPool

from demo_api.logging_setup import request_id_var

pool = ConnectionPool(os.environ["DEMO_DB_DSN"], min_size=2, max_size=20, open=False)


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with pool.connection() as conn:
        conn.execute("SELECT set_config('application_name', %s, false)", (f"demo-api:{request_id_var.get()}",))
        yield conn
