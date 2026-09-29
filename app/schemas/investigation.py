"""Pydantic schemas for investigation runs."""

import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict

from app.models.incident import IncidentState
from app.models.investigation import InvestigationRunStatus


class InvestigationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    status: InvestigationRunStatus
    triggered_by: uuid.UUID
    current_node: str | None
    outcome_state: IncidentState | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class InvestigationTriggerResponse(BaseModel):
    """Response for POST /api/incidents/{id}/investigate (202 Accepted)."""
    incident_id: uuid.UUID
    incident_state: IncidentState
    run: InvestigationRunRead
