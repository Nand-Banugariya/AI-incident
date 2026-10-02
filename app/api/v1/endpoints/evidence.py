"""Evidence read endpoint."""

import uuid
from datetime import datetime
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.models.evidence import Evidence, EvidenceType
from app.models.user import User
from app.schemas.evidence import EvidenceRead
from app.services.evidence_service import EvidenceService
from app.services.exceptions import NotFoundError

router = APIRouter(prefix="/incidents", tags=["Evidence"])


@router.get(
    "/{incident_id}/evidence",
    response_model=list[EvidenceRead],
    summary="List an incident's evidence",
    description="Evidence collected for the incident in time order, with optional filters. Requires 'evidence:read'.",
    responses={404: {"description": "Incident not found in the caller's organization"}},
)
def list_evidence(
    incident_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_permission("evidence:read"))],
    db: Annotated[Session, Depends(get_db)],
    evidence_type: Annotated[EvidenceType | None, Query()] = None,
    source_id: Annotated[uuid.UUID | None, Query()] = None,
    service: Annotated[str | None, Query(max_length=100)] = None,
    correlation_id: Annotated[str | None, Query(max_length=100)] = None,
    severity: Annotated[str | None, Query(max_length=50)] = None,
    start: Annotated[datetime | None, Query(description="Only evidence at or after this time")] = None,
    end: Annotated[datetime | None, Query(description="Only evidence at or before this time")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Evidence]:
    try:
        return EvidenceService.list(
            db=db,
            incident_id=incident_id,
            current_user=current_user,
            evidence_type=evidence_type,
            source_id=source_id,
            service=service,
            correlation_id=correlation_id,
            severity=severity,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
    except NotFoundError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
