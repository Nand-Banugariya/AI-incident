"""Demo API: a tiny order service with two planted faults (see ../ground_truth/)."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from demo_api import orders, refunds
from demo_api.db import pool
from demo_api.logging_setup import configure_logging, request_id_var

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True)
    logger.info("demo-api started")
    yield
    pool.close()


class RequestContextMiddleware:
    """Tags each request with an id (X-Request-ID or generated), logs it, and logs unhandled errors.

    Plain ASGI rather than @app.middleware("http"): BaseHTTPMiddleware chains an anyio EndOfStream onto
    every unhandled exception, which buries the real stack trace in the log.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = dict(scope["headers"]).get(b"x-request-id", b"").decode()[:32] or uuid.uuid4().hex[:8]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        state = {"status": 500, "sent": False}

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                state["status"], state["sent"] = message["status"], True
                message["headers"] = [*message.get("headers", []), (b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope["method"], scope["path"])
            if not state["sent"]:
                response = JSONResponse({"detail": "internal error", "request_id": request_id}, status_code=500)
                await response(scope, receive, send_with_id)
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            level = logging.WARNING if state["status"] >= 500 else logging.INFO
            logger.log(level, "%s %s -> %d in %dms", scope["method"], scope["path"], state["status"], elapsed_ms)
            request_id_var.reset(token)


app = FastAPI(title="demo-api", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/orders/search")
async def search_orders(email: str) -> list[dict]:
    try:
        return await run_in_threadpool(orders.search_orders_by_email, email)
    except psycopg.errors.QueryCanceled:
        logger.exception("Order search timed out")
        raise HTTPException(status_code=503, detail="search timed out") from None


@app.get("/orders/{order_id}")
async def get_order(order_id: int) -> dict:
    order = await run_in_threadpool(orders.get_order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@app.post("/refunds/{order_id}")
async def create_refund(order_id: int) -> dict:
    refund = await run_in_threadpool(refunds.create_refund, order_id)
    if refund is None:
        raise HTTPException(status_code=404, detail="order not found")
    return refund
