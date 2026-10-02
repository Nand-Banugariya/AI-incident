"""Audit log of every tool call an agent node makes (§0 Auditability, §2).

Write-capable calls are flagged so one query can prove each has a matching approval at or before its start.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin


_WRITE_PREDICATE = text("is_write_operation = true")


class AgentToolCall(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "agent_tool_calls"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investigation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_node: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "database_investigation"
    mcp_server: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "database-mcp"
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    output_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)  # truncated; full payload is evidence
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    is_write_operation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_agent_tool_calls_incident_started", "incident_id", "started_at"),
        # The scheduled integrity check scans only write operations
        Index(
            "ix_agent_tool_calls_write_ops",
            "started_at",
            postgresql_where=_WRITE_PREDICATE,
            sqlite_where=_WRITE_PREDICATE,
        ),
    )
