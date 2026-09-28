"""Pydantic schemas for Incident request validation and response serialization."""

import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.incident import IncidentState


class IncidentBase(BaseModel):
    environment: str = Field(..., example="production", min_length=2, max_length=50)
    service: str | None = Field(default=None, example="payment-service", max_length=100)
    start_time: datetime = Field(..., description="Timestamp marking the beginning of the incident window")
    end_time: datetime = Field(..., description="Timestamp marking the end of the incident window")
    error_hint: str | None = Field(default=None, example="500 Internal Server Error spikes on /checkout", max_length=500)
    correlation_id: str | None = Field(default=None, example="req-7b89d4fe-9c21", max_length=100)

    @model_validator(mode="after")
    def validate_time_window(self) -> "IncidentBase":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be strictly after start_time")
        return self


class IncidentCreate(IncidentBase):
    """Payload for POST /api/incidents."""
    pass


class IncidentRead(IncidentBase):
    """Response model for Incident representation."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    state: IncidentState
    langgraph_thread_id: str
    created_by: uuid.UUID
    total_llm_cost_usd: Decimal
    created_at: datetime
    updated_at: datetime
