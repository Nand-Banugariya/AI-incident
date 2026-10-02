"""Human approval records — the single source of truth for every write-capable action (§2, §4, §8).

No flag, cache or session variable may stand in for a row here. History is append-only: a rejection
or a re-approval creates a NEW row and never rewrites an old one, so the full trail survives for audit.
"""

import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.incident import RiskLevel
from app.models.base import UUIDPrimaryKeyMixin


class ApprovalStage(str, enum.Enum):
    PLAN = "plan"
    CODE = "code"
    PRODUCTION = "production"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# SQLAlchemy persists enum NAMES, so DB-level predicates match on names
_PENDING_PREDICATE = text("status = 'PENDING'")


class Approval(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "incident_approvals"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[ApprovalStage] = mapped_column(Enum(ApprovalStage), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus),
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # required when rejected
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The incident's risk level when this decision was made, so the audit trail shows why a rule applied
    risk_level: Mapped[RiskLevel | None] = mapped_column(Enum(RiskLevel), nullable=True)
    # Set in Python (microsecond precision) as well as by the DB: "latest approval wins" depends on this
    # ordering, and a second-resolution timestamp could tie when a rejection is followed by a re-approval.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # At most one open request per incident+stage; deciding it (or re-requesting after a decision) is a new row
        Index(
            "uq_incident_approvals_one_pending_per_stage",
            "incident_id",
            "stage",
            unique=True,
            postgresql_where=_PENDING_PREDICATE,
            sqlite_where=_PENDING_PREDICATE,
        ),
        # Lookup path of require_approval(): newest row for an incident+stage
        Index("ix_incident_approvals_incident_stage_created", "incident_id", "stage", "created_at"),
        # A rejection must explain itself
        CheckConstraint("status != 'REJECTED' OR (reason IS NOT NULL AND reason != '')", name="rejected_requires_reason"),
        # A decision must record who and when
        CheckConstraint(
            "status = 'PENDING' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="decided_requires_actor",
        ),
    )
