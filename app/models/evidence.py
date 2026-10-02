"""Evidence collected for an incident. Every provider's output is normalised into these rows (§2)."""

import enum
import uuid
from datetime import datetime
from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin


class EvidenceType(str, enum.Enum):
    APPLICATION_LOG = "application_log"
    DATABASE_LOG = "database_log"
    STACK_TRACE = "stack_trace"
    QUERY = "query"
    KUBERNETES_EVENT = "kubernetes_event"
    SERVER_EVENT = "server_event"
    DEPLOYMENT = "deployment"
    GIT_COMMIT = "git_commit"
    SOURCE_CODE = "source_code"
    METRIC = "metric"
    API_RESPONSE = "api_response"


class Evidence(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "incident_evidence"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RESTRICT: a source that produced evidence cannot be deleted out from under the audit trail
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(Enum(EvidenceType), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # when it happened at the source
    service: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str | None] = mapped_column(Text, nullable=True)  # after the log pipeline
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    severity: Mapped[str | None] = mapped_column(String(50), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex; makes re-runs idempotent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # Re-running an investigation must not duplicate evidence (§0 Idempotency)
        UniqueConstraint("incident_id", "content_hash", name="uq_incident_evidence_hash"),
        # Listing path: one incident's evidence in time order
        Index("ix_incident_evidence_incident_timestamp", "incident_id", "timestamp"),
    )
