"""Aggregated v1 API Router."""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, incidents

api_router = APIRouter(prefix="/api")

# Register routers
api_router.include_router(auth.router)
api_router.include_router(incidents.router)
