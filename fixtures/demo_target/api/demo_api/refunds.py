"""Refund requests."""

import logging
from datetime import datetime, timedelta

from demo_api.db import connection

logger = logging.getLogger(__name__)


def load_refund_policy(conn) -> dict | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'refund_policy'").fetchone()
    return row[0] if row else None


def calculate_refund_deadline(policy: dict, ordered_at: datetime) -> datetime:
    return ordered_at + timedelta(days=policy["window_days"])


def create_refund(order_id: int) -> dict | None:
    with connection() as conn:
        order = conn.execute("SELECT id, total_cents, created_at FROM orders WHERE id = %s", (order_id,)).fetchone()
        if order is None:
            return None
        policy = load_refund_policy(conn)
        deadline = calculate_refund_deadline(policy, order[2])
        refund_id = conn.execute(
            "INSERT INTO refunds (order_id, amount_cents, deadline) VALUES (%s, %s, %s) RETURNING id",
            (order_id, order[1], deadline),
        ).fetchone()[0]
    logger.info("Refund %s created for order %s", refund_id, order_id)
    return {"refund_id": refund_id, "order_id": order_id, "deadline": deadline.isoformat()}
