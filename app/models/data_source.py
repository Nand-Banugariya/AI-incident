"""Data source registry: where evidence can be pulled from, what each source can do, and whether it is reachable (§2)."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class DataSourceHealthStatus(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


class DataSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "data_sources"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )  # tenant boundary: one org's credentials must never be reachable from another org's incident
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "kubernetes" | "postgresql" | "linux_server" | ...
    environment: Mapped[str] = mapped_column(String(50), nullable=False)
    # host, namespace, log paths — NEVER credentials
    configuration_json: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    credential_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)  # opaque pointer into SecretResolver
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization = relationship("Organization", back_populates="data_sources")
    capabilities = relationship("DataSourceCapability", back_populates="data_source", cascade="all, delete-orphan")
    health_checks = relationship("DataSourceHealth", back_populates="data_source", cascade="all, delete-orphan")

    @property
    def has_credential(self) -> bool:
        return bool(self.credential_reference)


class DataSourceCapability(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "data_source_capabilities"

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability: Mapped[str] = mapped_column(String(100), nullable=False)  # "application_logs", "database_logs", ...
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    data_source = relationship("DataSource", back_populates="capabilities")

    __table_args__ = (UniqueConstraint("data_source_id", "capability", name="uq_data_source_capability"),)


class DataSourceHealth(Base, UUIDPrimaryKeyMixin):
    """Append-only history of health checks; the latest row per source drives the Planning node."""

    __tablename__ = "data_source_health"

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[DataSourceHealthStatus] = mapped_column(Enum(DataSourceHealthStatus), nullable=False)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    data_source = relationship("DataSource", back_populates="health_checks")

    __table_args__ = (Index("ix_data_source_health_source_checked", "data_source_id", "last_checked_at"),)
