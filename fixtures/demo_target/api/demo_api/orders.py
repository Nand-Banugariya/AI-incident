"""Order lookups."""

import logging

from demo_api.db import connection

logger = logging.getLogger(__name__)

PAGE_SIZE = 20


def get_order(order_id: int) -> dict | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT id, customer_email, status, total_cents, created_at FROM orders WHERE id = %s", (order_id,)
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "customer_email": row[1], "status": row[2], "total_cents": row[3],
            "created_at": row[4].isoformat()}


def search_orders_by_email(email: str) -> list[dict]:
    """Recent orders for a customer email (support-desk search).

    Filters on orders.customer_email, which has no index, so every call is a full scan of the orders table.
    """
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, status, total_cents, created_at FROM orders "
            "WHERE customer_email = %s ORDER BY created_at DESC LIMIT %s",
            (email, PAGE_SIZE),
        ).fetchall()
    logger.info("Order search for email returned %d rows", len(rows))
    return [{"id": r[0], "status": r[1], "total_cents": r[2], "created_at": r[3].isoformat()} for r in rows]
