"""Pydantic schemas for approval decisions and history."""

import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.approval import ApprovalStage, ApprovalStatus
from app.models.incident import RiskLevel


class ApprovalApproveRequest(BaseModel):
    """Optional body for POST /api/incidents/{id}/approve-*."""

    reason: str | None = Field(default=None, max_length=1000, description="Optional note recorded with the approval.")

    @field_validator("reason")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class ApprovalRejectRequest(BaseModel):
    """Body for POST /api/incidents/{id}/reject-*. A rejection must explain itself."""

    reason: str = Field(..., max_length=1000, example="Rollback steps are missing from the plan")

    @field_validator("reason")
    @classmethod
    def _require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    stage: ApprovalStage
    status: ApprovalStatus
    decided_by: uuid.UUID | None
    reason: str | None
    risk_level: RiskLevel | None
    decided_at: datetime | None
    created_at: datetime
