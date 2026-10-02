"""Read-only incident views: status snapshot and the audit timeline."""

import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.models.user import User
from app.schemas.activity import IncidentStatusRead, TimelineEvent
from app.services.activity_service import ActivityService
from app.services.exceptions import NotFoundError

router = APIRouter(prefix="/incidents", tags=["Incident Activity"])

_NOT_FOUND = {404: {"description": "Incident not found in the caller's organization"}}


@router.get(
    "/{incident_id}/status",
    response_model=IncidentStatusRead,
    summary="Incident status snapshot",
    description=(
        "Current state, risk level, the approval gate the incident is waiting at (if any), LLM spend, "
        "evidence count and the latest investigation run. Requires 'incidents:read'."
    ),
    responses=_NOT_FOUND,
)
def get_incident_status(
    incident_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_permission("incidents:read"))],
    db: Annotated[Session, Depends(get_db)],
) -> IncidentStatusRead:
    try:
        return ActivityService.status(db=db, incident_id=incident_id, current_user=current_user)
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))


@router.get(
    "/{incident_id}/timeline",
    response_model=list[TimelineEvent],
    summary="Incident audit timeline",
    description=(
        "Oldest-first merge of state transitions, approval requests/decisions and agent tool calls, rebuilt "
        "from the database. Tool inputs are not included. Requires 'incidents:read'."
    ),
    responses=_NOT_FOUND,
)
def get_incident_timeline(
    incident_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_permission("incidents:read"))],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TimelineEvent]:
    try:
        return ActivityService.timeline(
            db=db, incident_id=incident_id, current_user=current_user, limit=limit, offset=offset
        )
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
