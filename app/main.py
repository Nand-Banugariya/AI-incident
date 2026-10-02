"""FastAPI Application Entrypoint with production lifecycle, CORS, and standardized errors."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import engine
from app.core.logging import configure_logging
import app.models  # Ensure all models are registered with Base.metadata

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown events."""
    # Schema is managed by Alembic (`alembic upgrade head`), not created at startup
    yield
    # Shutdown actions: gracefully close database connections
    engine.dispose()


app = FastAPI(
    title="AI Production Incident Investigation Platform",
    description="Autonomous incident investigation and remediation platform with LangGraph, MCP, and multi-stage human approval gates.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS Middleware
if "*" in settings.CORS_ALLOWED_ORIGINS:
    raise RuntimeError("CORS_ALLOWED_ORIGINS must list explicit origins; '*' is not allowed")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health", tags=["Health"], summary="System Health Check")
def health_check() -> dict[str, str]:
    """Lightweight endpoint for liveness and readiness probes."""
    return {"status": "healthy", "service": "incident-investigation-platform"}


# Global exception handler matching specification §8
@app.exception_handler(Exception)
def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Full exception is logged server-side only; never expose internals to the client
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "detail": "An unexpected error occurred",
            "incident_id": request.path_params.get("incident_id"),
        },
    )


# Mount the aggregated API router
app.include_router(api_router)
