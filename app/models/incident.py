"""Incident model and state definitions as defined in the platform specification."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class IncidentState(str, enum.Enum):
    CREATED = "created"
    PLANNING = "planning"
    COLLECTING_EVIDENCE = "collecting_evidence"
    CORRELATING = "correlating"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    IMPLEMENTATION_PLAN_READY = "implementation_plan_ready"
    WAITING_PLAN_APPROVAL = "waiting_plan_approval"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    IMPLEMENTATION_READY = "implementation_ready"
    WAITING_CODE_APPROVAL = "waiting_code_approval"
    PR_CREATED = "pr_created"
    QA = "qa"
    WAITING_PRODUCTION_APPROVAL = "waiting_production_approval"
    DEPLOYING = "deploying"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    INVESTIGATION_FAILED = "investigation_failed"
    NEEDS_MORE_INVESTIGATION = "needs_more_investigation"


class Incident(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "incidents"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    environment: Mapped[str] = mapped_column(String(50), nullable=False)  # "production", "staging"
    service: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_hint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    state: Mapped[IncidentState] = mapped_column(
        Enum(IncidentState),
        nullable=False,
        default=IncidentState.CREATED,
        index=True,
    )
    langgraph_thread_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )  # one thread per incident
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    total_llm_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        default=Decimal("0.0000"),
    )

    # Relationships
    organization = relationship("Organization", back_populates="incidents")
    creator = relationship("User", back_populates="incidents_created")
