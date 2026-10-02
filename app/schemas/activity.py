"""Pydantic schemas for an incident's status snapshot and its audit timeline."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel

from app.models.approval import ApprovalStage
from app.models.incident import IncidentState, RiskLevel
from app.schemas.investigation import InvestigationRunRead

TimelineKind = Literal["state_transition", "approval_requested", "approval_decision", "tool_call"]


class IncidentStatusRead(BaseModel):
    """GET /api/incidents/{id}/status: everything a UI needs to show where an incident stands."""

    incident_id: uuid.UUID
    state: IncidentState
    risk_level: RiskLevel | None
    awaiting_approval: ApprovalStage | None  # set while the incident is parked at a human gate
    total_llm_cost_usd: Decimal
    evidence_count: int
    latest_run: InvestigationRunRead | None
    updated_at: datetime


class TimelineEvent(BaseModel):
    id: str  # "<kind>:<uuid>"
    timestamp: datetime
    kind: TimelineKind
    summary: str
    actor_id: uuid.UUID | None  # None = the system (worker / agent)
    details: dict[str, Any]
