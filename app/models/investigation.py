"""Investigation run tracking and incident state-transition audit trail (§0 Auditability, §0 Idempotency)."""

import enum
import uuid
from datetime import datetime
from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.incident import IncidentState


class InvestigationRunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


ACTIVE_RUN_STATUSES = (InvestigationRunStatus.QUEUED, InvestigationRunStatus.RUNNING)

# SQLAlchemy persists enum NAMES, so the partial-index predicate matches on names
_ACTIVE_RUN_PREDICATE = text("status IN ('QUEUED', 'RUNNING')")


class InvestigationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per /investigate trigger. The run id doubles as the Celery task id."""

    __tablename__ = "investigation_runs"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    triggered_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[InvestigationRunStatus] = mapped_column(
        Enum(InvestigationRunStatus),
        nullable=False,
        default=InvestigationRunStatus.QUEUED,
        index=True,
    )
    current_node: Mapped[str | None] = mapped_column(String(100), nullable=True)
    outcome_state: Mapped[IncidentState | None] = mapped_column(Enum(IncidentState), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # DB-level guarantee: at most one queued/running investigation per incident
        Index(
            "uq_investigation_runs_one_active_per_incident",
            "incident_id",
            unique=True,
            postgresql_where=_ACTIVE_RUN_PREDICATE,
            sqlite_where=_ACTIVE_RUN_PREDICATE,
        ),
    )


class IncidentStateTransition(Base, UUIDPrimaryKeyMixin):
    """Append-only audit log of every incident state change."""

    __tablename__ = "incident_state_transitions"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    from_state: Mapped[IncidentState | None] = mapped_column(Enum(IncidentState), nullable=True)
    to_state: Mapped[IncidentState] = mapped_column(Enum(IncidentState), nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )  # NULL = system (investigation worker)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
