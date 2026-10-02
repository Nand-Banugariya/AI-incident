"""Service layer for incident evidence: idempotent recording and querying (§0 Idempotency, §2).

`record()` is the single entry point providers/nodes use to store evidence. It dedupes on a content hash,
so re-running an investigation never duplicates rows, and it refuses a data source from another
organization, so one tenant's sources can never feed another tenant's incident.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.data_source import DataSource
from app.models.evidence import Evidence, EvidenceType
from app.models.incident import Incident
from app.models.user import User
from app.services.exceptions import ForbiddenError, NotFoundError

logger = logging.getLogger(__name__)


def compute_content_hash(
    *, source_id: uuid.UUID, evidence_type: EvidenceType, timestamp: datetime, content: str
) -> str:
    """Stable identity of one piece of evidence: same source + type + moment + content => same hash.

    The source is part of the identity on purpose: the same line seen by two different sources is two
    observations, not one. Naive timestamps are taken as UTC so the hash is the same on every database.
    """
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    canonical = "\x1f".join(
        [str(source_id), evidence_type.value, timestamp.astimezone(timezone.utc).isoformat(), content]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EvidenceService:
    @staticmethod
    def _find(db: Session, incident_id: uuid.UUID, content_hash: str) -> Evidence | None:
        return db.scalar(
            select(Evidence).where(Evidence.incident_id == incident_id, Evidence.content_hash == content_hash)
        )

    @staticmethod
    def existing_hashes(db: Session, incident_id: uuid.UUID) -> set[str]:
        """Hashes already stored for an incident, so a re-run can skip fetching what it already has."""
        return set(db.scalars(select(Evidence.content_hash).where(Evidence.incident_id == incident_id)))

    @staticmethod
    def record(
        db: Session,
        *,
        incident: Incident,
        source: DataSource,
        evidence_type: EvidenceType,
        timestamp: datetime,
        content: str,
        service: str | None = None,
        normalized_content: str | None = None,
        correlation_id: str | None = None,
        severity: str | None = None,
    ) -> tuple[Evidence, bool]:
        """Stores one piece of evidence unless it already exists. Returns (row, created).

        Does not commit: the caller owns the transaction, so a whole collection step commits (or rolls back) together.
        """
        if source.org_id != incident.org_id:
            logger.error(
                "Cross-tenant evidence blocked: source=%s (org %s) incident=%s (org %s)",
                source.id, source.org_id, incident.id, incident.org_id,
            )
            raise ForbiddenError("Data source does not belong to the incident's organization")

        content_hash = compute_content_hash(
            source_id=source.id, evidence_type=evidence_type, timestamp=timestamp, content=content
        )
        existing = EvidenceService._find(db, incident.id, content_hash)
        if existing is not None:
            return existing, False

        evidence = Evidence(
            incident_id=incident.id,
            source_id=source.id,
            evidence_type=evidence_type,
            timestamp=timestamp,
            service=service,
            content=content,
            normalized_content=normalized_content,
            correlation_id=correlation_id,
            severity=severity,
            content_hash=content_hash,
        )
        try:
            with db.begin_nested():  # savepoint: a lost race must not poison the caller's transaction
                db.add(evidence)
                db.flush()
        except IntegrityError:
            # A concurrent run stored the same evidence between our check and our insert
            existing = EvidenceService._find(db, incident.id, content_hash)
            if existing is None:
                raise
            return existing, False
        return evidence, True

    @staticmethod
    def count(db: Session, incident_id: uuid.UUID) -> int:
        return db.scalar(select(func.count()).select_from(Evidence).where(Evidence.incident_id == incident_id)) or 0

    @staticmethod
    def list(
        db: Session,
        incident_id: uuid.UUID,
        current_user: User,
        *,
        evidence_type: EvidenceType | None = None,
        source_id: uuid.UUID | None = None,
        service: str | None = None,
        correlation_id: str | None = None,
        severity: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Evidence]:
        """One incident's evidence in time order. 404 if the incident is outside the caller's org."""
        in_scope = db.scalar(
            select(Incident.id).where(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        )
        if in_scope is None:
            raise NotFoundError(f"Incident with ID '{incident_id}' not found")

        query = select(Evidence).where(Evidence.incident_id == incident_id)
        filters = {
            Evidence.evidence_type: evidence_type,
            Evidence.source_id: source_id,
            Evidence.service: service,
            Evidence.correlation_id: correlation_id,
            Evidence.severity: severity.lower() if severity else None,
        }
        for column, value in filters.items():
            if value is not None:
                query = query.where(column == value)
        if start is not None:
            query = query.where(Evidence.timestamp >= start)
        if end is not None:
            query = query.where(Evidence.timestamp <= end)
        query = query.order_by(Evidence.timestamp, Evidence.id).limit(limit).offset(offset)
        return list(db.execute(query).scalars().all())


def record_many(db: Session, items: Iterable[dict]) -> tuple[int, int]:
    """Records a batch; returns (created, skipped_as_duplicates)."""
    created = skipped = 0
    for item in items:
        _, was_created = EvidenceService.record(db, **item)
        created, skipped = (created + 1, skipped) if was_created else (created, skipped + 1)
    return created, skipped
