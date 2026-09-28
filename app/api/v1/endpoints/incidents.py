"""Incident API endpoints: Creation and retrieval."""

import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.models.incident import Incident
from app.models.user import User
from app.schemas.incident import IncidentCreate, IncidentRead
from app.services.incident_service import IncidentService

router = APIRouter(prefix="/incidents", tags=["Incidents"])


@router.post(
    "",
    response_model=IncidentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new incident",
    description="Registers a new production/staging incident scoped to the caller's organization. Requires 'incidents:create' permission.",
)
def create_incident(
    payload: IncidentCreate,
    current_user: Annotated[User, Depends(require_permission("incidents:create"))],
    db: Annotated[Session, Depends(get_db)],
) -> Incident:
    """Creates a new incident record and allocates an isolated LangGraph thread ID."""
    incident = IncidentService.create_incident(
        db=db,
        payload=payload,
        current_user=current_user,
    )
    return incident


@router.get(
    "",
    response_model=list[IncidentRead],
    summary="List all incidents",
    description="Retrieves a list of incidents scoped to the caller's organization. Requires 'incidents:read' permission.",
)
def list_incidents(
    current_user: Annotated[User, Depends(require_permission("incidents:read"))],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Incident]:
    """Lists incidents filtered by the user's organization."""
    query = (
        select(Incident)
        .where(Incident.org_id == current_user.org_id)
        .order_by(Incident.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = db.execute(query)
    return list(result.scalars().all())


@router.get(
    "/{incident_id}",
    response_model=IncidentRead,
    summary="Get incident by ID",
    description="Retrieves single incident details. Requires 'incidents:read' permission.",
)
def get_incident(
    incident_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_permission("incidents:read"))],
    db: Annotated[Session, Depends(get_db)],
) -> Incident:
    """Fetches single incident details, verifying organization tenant boundaries."""
    query = select(Incident).where(
        Incident.id == incident_id,
        Incident.org_id == current_user.org_id,
    )
    result = db.execute(query)
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident with ID '{incident_id}' not found",
        )
    return incident
