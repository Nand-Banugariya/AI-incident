"""Incident API endpoints: Creation, retrieval, and deletion."""

import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission, require_role
from app.models.incident import Incident
from app.models.user import User
from app.schemas.incident import IncidentCreate, IncidentRead
from app.schemas.investigation import InvestigationRunRead, InvestigationTriggerResponse
from app.security.rbac import Role
from app.services.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.services.incident_service import IncidentService
from app.services.investigation_service import InvestigationService

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


@router.delete(
    "/{incident_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete incident by ID",
    description="Permanently deletes an incident within the caller's organization. Requires the 'admin' role.",
)
def delete_incident(
    incident_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_role(Role.ADMIN))],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Deletes an incident, verifying organization tenant boundaries."""
    try:
        deleted = IncidentService.delete_incident(
            db=db,
            incident_id=incident_id,
            current_user=current_user,
        )
    except ConflictError as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err))
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident with ID '{incident_id}' not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{incident_id}/investigate",
    response_model=InvestigationTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an investigation",
    description=(
        "Queues a background LangGraph investigation for the incident. Allowed only when the incident is "
        "'created', 'needs_more_investigation' or 'investigation_failed'; returns 409 if an investigation "
        "is already running. Requires 'incidents:create' permission."
    ),
    responses={
        404: {"description": "Incident not found in the caller's organization"},
        409: {"description": "Incident state does not allow a new investigation"},
        503: {"description": "Task queue unavailable; incident state was restored, safe to retry"},
    },
)
def investigate_incident(
    incident_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_permission("incidents:create"))],
    db: Annotated[Session, Depends(get_db)],
) -> InvestigationTriggerResponse:
    """Triggers the investigation worker for an incident."""
    try:
        run, incident = InvestigationService.trigger(db=db, incident_id=incident_id, current_user=current_user)
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    except ConflictError as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err))
    except ServiceUnavailableError as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(err),
            headers={"Retry-After": "30"},
        )

    return InvestigationTriggerResponse(
        incident_id=incident.id,
        incident_state=incident.state,
        run=InvestigationRunRead.model_validate(run),
    )
