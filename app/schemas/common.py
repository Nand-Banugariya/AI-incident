"""Common Pydantic schemas for standard responses and error envelopes."""

from typing import Any, Generic, TypeVar
from pydantic import BaseModel, Field

DataT = TypeVar("DataT")


class StandardResponse(BaseModel, Generic[DataT]):
    """Standardized API response wrapper."""
    success: bool = True
    message: str | None = None
    data: DataT


class ErrorEnvelope(BaseModel):
    """Standard error response format matching specification §8."""
    error: str = Field(..., description="Short machine-readable error code")
    detail: str = Field(..., description="Human-readable explanation")
    incident_id: str | None = Field(default=None, description="Related incident ID if applicable")
