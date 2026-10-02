"""Aggregated v1 API Router."""

from fastapi import APIRouter

from app.api.v1.endpoints import approvals, auth, data_sources, evidence, gated_actions, incident_activity, incidents

api_router = APIRouter(prefix="/api")

# Register routers
api_router.include_router(auth.router)
api_router.include_router(incidents.router)
api_router.include_router(approvals.router)
api_router.include_router(gated_actions.router)
api_router.include_router(incident_activity.router)
api_router.include_router(evidence.router)
api_router.include_router(data_sources.router)
