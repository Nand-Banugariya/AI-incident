"""Service layer for data source registration, capability management and connection tests (§2)."""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.data_source import (
    DataSource,
    DataSourceCapability,
    DataSourceHealth,
    DataSourceHealthStatus,
)
from app.models.evidence import Evidence
from app.models.user import User
from app.schemas.data_source import DataSourceCreate, DataSourceUpdate
from app.services.exceptions import ConflictError, NotFoundError, NotSupportedError

logger = logging.getLogger(__name__)

# provider_type -> probe(data_source). A probe returns None when healthy, or raises on failure.
# Providers register themselves here as their MCP servers land (Phase 2/3); until then /test returns 501.
ConnectionProbe = Callable[[DataSource], None]
CONNECTION_PROBES: dict[str, ConnectionProbe] = {}

_MAX_ERROR_LENGTH = 500


class DataSourceService:
    """Every query is scoped to the caller's org so one tenant's sources are never reachable from another."""

    @staticmethod
    def _get_scoped(db: Session, data_source_id: uuid.UUID, current_user: User) -> DataSource:
        data_source = db.execute(
            select(DataSource)
            .options(selectinload(DataSource.capabilities))
            .where(DataSource.id == data_source_id, DataSource.org_id == current_user.org_id)
        ).scalar_one_or_none()
        if not data_source:
            raise NotFoundError(f"Data source with ID '{data_source_id}' not found")
        return data_source

    @staticmethod
    def get(db: Session, data_source_id: uuid.UUID, current_user: User) -> DataSource:
        return DataSourceService._get_scoped(db, data_source_id, current_user)

    @staticmethod
    def list(
        db: Session,
        current_user: User,
        *,
        environment: str | None = None,
        provider_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DataSource]:
        query = (
            select(DataSource)
            .options(selectinload(DataSource.capabilities))
            .where(DataSource.org_id == current_user.org_id)
            .order_by(DataSource.created_at.desc(), DataSource.id)
            .limit(limit)
            .offset(offset)
        )
        if environment:
            query = query.where(DataSource.environment == environment.strip().lower())
        if provider_type:
            query = query.where(DataSource.provider_type == provider_type.strip().lower())
        return list(db.execute(query).scalars().all())

    @staticmethod
    def create(db: Session, payload: DataSourceCreate, current_user: User) -> DataSource:
        data_source = DataSource(
            org_id=current_user.org_id,
            name=payload.name,
            provider_type=payload.provider_type,
            environment=payload.environment,
            configuration_json=payload.configuration_json,
            credential_reference=payload.credential_reference,
            is_enabled=payload.is_enabled,
            capabilities=[DataSourceCapability(capability=name, is_enabled=True) for name in payload.capabilities],
        )
        db.add(data_source)
        db.commit()
        db.refresh(data_source)
        logger.info(
            "Data source %s (%s) created by user %s (org %s)",
            data_source.id, data_source.provider_type, current_user.id, current_user.org_id,
        )
        return data_source

    @staticmethod
    def update(
        db: Session, data_source_id: uuid.UUID, payload: DataSourceUpdate, current_user: User
    ) -> DataSource:
        data_source = DataSourceService._get_scoped(db, data_source_id, current_user)
        changes = payload.model_dump(exclude_unset=True)

        # name/environment/configuration_json/is_enabled are NOT NULL: an explicit null means "no change".
        for field in ("name", "environment", "configuration_json", "is_enabled"):
            if changes.get(field) is not None:
                setattr(data_source, field, changes[field])
        # credential_reference is nullable: an explicit null clears it.
        if "credential_reference" in changes:
            data_source.credential_reference = changes["credential_reference"]

        if changes.get("capabilities") is not None:
            wanted = set(changes["capabilities"])
            for existing in list(data_source.capabilities):
                if existing.capability not in wanted:
                    data_source.capabilities.remove(existing)
            present = {c.capability for c in data_source.capabilities}
            for name in changes["capabilities"]:
                if name not in present:
                    data_source.capabilities.append(DataSourceCapability(capability=name, is_enabled=True))

        db.commit()
        db.refresh(data_source)
        logger.info(
            "Data source %s updated by user %s (fields: %s)",
            data_source.id, current_user.id, ", ".join(sorted(changes)) or "none",
        )
        return data_source

    @staticmethod
    def delete(db: Session, data_source_id: uuid.UUID, current_user: User) -> None:
        data_source = DataSourceService._get_scoped(db, data_source_id, current_user)
        if db.scalar(select(func.count()).select_from(Evidence).where(Evidence.source_id == data_source.id)):
            raise ConflictError(
                "This data source has collected evidence and cannot be deleted (the audit trail depends on it); "
                "disable it instead"
            )
        db.delete(data_source)
        db.commit()
        logger.info("Data source %s deleted by user %s (org %s)", data_source_id, current_user.id, current_user.org_id)

    @staticmethod
    def test_connection(db: Session, data_source_id: uuid.UUID, current_user: User) -> DataSourceHealth:
        """Runs the provider's probe and records the result in data_source_health."""
        data_source = DataSourceService._get_scoped(db, data_source_id, current_user)
        probe = CONNECTION_PROBES.get(data_source.provider_type)
        if probe is None:
            raise NotSupportedError(
                f"Connection testing is not available yet for provider type '{data_source.provider_type}'"
            )

        started = time.perf_counter()
        error: str | None = None
        try:
            probe(data_source)
        except Exception as exc:  # a failing probe is a result to record, not a server error
            logger.warning("Connection test failed for data source %s: %s", data_source.id, exc)
            error = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_LENGTH]
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        health = DataSourceHealth(
            data_source_id=data_source.id,
            status=DataSourceHealthStatus.UNREACHABLE if error else DataSourceHealthStatus.HEALTHY,
            last_checked_at=datetime.now(timezone.utc),
            error_message=error,
            response_time_ms=elapsed_ms,
        )
        db.add(health)
        db.commit()
        db.refresh(health)
        return health
