"""Pydantic schemas for evidence responses."""

import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict

from app.models.evidence import EvidenceType


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    source_id: uuid.UUID
    evidence_type: EvidenceType
    timestamp: datetime
    service: str | None
    content: str
    normalized_content: str | None
    correlation_id: str | None
    severity: str | None
    created_at: datetime
