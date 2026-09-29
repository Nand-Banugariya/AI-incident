"""Service layer for triggering and tracking LangGraph investigation runs."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.incident import Incident, IncidentState
from app.models.investigation import (
    ACTIVE_RUN_STATUSES,
    IncidentStateTransition,
    InvestigationRun,
    InvestigationRunStatus,
)
from app.models.user import User
from app.services.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.worker.celery_app import INVESTIGATION_TASK_NAME, celery_app

logger = logging.getLogger(__name__)

# States from which a (re-)investigation may be started
INVESTIGATION_START_STATES = frozenset({
    IncidentState.CREATED,
    IncidentState.NEEDS_MORE_INVESTIGATION,
    IncidentState.INVESTIGATION_FAILED,
})

MAX_ERROR_LENGTH = 2000


@dataclass(frozen=True)
class RunContext:
    """Everything the worker needs to execute (or resume) one investigation run."""
    run_id: uuid.UUID
    thread_id: str
    resume: bool
    initial_state: dict[str, Any]


def record_transition(
    db: Session,
    incident: Incident,
    to_state: IncidentState,
    *,
    changed_by: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> None:
    """Sets the incident state and appends an audit row (no-op if the state is unchanged)."""
    if incident.state == to_state:
        return
    db.add(
        IncidentStateTransition(
            incident_id=incident.id,
            run_id=run_id,
            from_state=incident.state,
            to_state=to_state,
            changed_by=changed_by,
            reason=reason[:500] if reason else None,
        )
    )
    incident.state = to_state


class InvestigationService:
    """Encapsulates the investigation lifecycle: trigger (API side) and run bookkeeping (worker side)."""

    # ── API side ────────────────────────────────────────────────────────────

    @staticmethod
    def trigger(db: Session, incident_id: uuid.UUID, current_user: User) -> tuple[InvestigationRun, Incident]:
        """Atomically moves the incident to PLANNING, records a QUEUED run, and enqueues the worker task."""
        incident = db.execute(
            select(Incident).where(
                Incident.id == incident_id,
                Incident.org_id == current_user.org_id,
            )
        ).scalar_one_or_none()
        if not incident:
            raise NotFoundError(f"Incident with ID '{incident_id}' not found")

        previous_state = incident.state
        if previous_state not in INVESTIGATION_START_STATES:
            raise ConflictError(
                f"Incident is in state '{previous_state.value}', cannot start an investigation"
            )

        # Compare-and-set guards against two concurrent triggers both passing the check above
        claimed = db.execute(
            update(Incident)
            .where(Incident.id == incident.id, Incident.state == previous_state)
            .values(state=IncidentState.PLANNING)
            .execution_options(synchronize_session=False)
        ).rowcount
        if claimed != 1:
            db.rollback()
            raise ConflictError("Incident state changed concurrently, retry the request")

        run = InvestigationRun(
            incident_id=incident.id,
            triggered_by=current_user.id,
            status=InvestigationRunStatus.QUEUED,
        )
        db.add(run)
        try:
            db.flush()
        except IntegrityError:
            # uq_investigation_runs_one_active_per_incident — second line of defence
            db.rollback()
            raise ConflictError("An investigation is already in progress for this incident")

        db.add(
            IncidentStateTransition(
                incident_id=incident.id,
                run_id=run.id,
                from_state=previous_state,
                to_state=IncidentState.PLANNING,
                changed_by=current_user.id,
                reason="Investigation triggered",
            )
        )
        db.commit()
        db.refresh(incident)
        db.refresh(run)

        # Enqueue only after commit so the worker never sees an uncommitted run
        try:
            celery_app.send_task(
                INVESTIGATION_TASK_NAME,
                args=[str(run.id)],
                task_id=str(run.id),
                retry=True,
                retry_policy={
                    "max_retries": settings.TASK_ENQUEUE_MAX_RETRIES,
                    "interval_start": 0,
                    "interval_step": 0.5,
                    "interval_max": 1,
                },
            )
        except Exception:
            logger.exception("Failed to enqueue investigation run %s for incident %s", run.id, incident.id)
            InvestigationService._compensate_enqueue_failure(db, run, incident, previous_state, current_user)
            raise ServiceUnavailableError("Investigation queue is unavailable, try again shortly")

        logger.info(
            "Investigation run %s queued for incident %s by user %s",
            run.id, incident.id, current_user.id,
        )
        return run, incident

    @staticmethod
    def _compensate_enqueue_failure(
        db: Session,
        run: InvestigationRun,
        incident: Incident,
        previous_state: IncidentState,
        current_user: User,
    ) -> None:
        """Rolls the incident back to its prior state so the trigger can be retried."""
        run.status = InvestigationRunStatus.FAILED
        run.error = "Task queue unavailable; run was never started"
        run.completed_at = datetime.now(timezone.utc)
        record_transition(
            db, incident, previous_state,
            changed_by=current_user.id, run_id=run.id, reason="Enqueue failed; state restored",
        )
        db.commit()

    @staticmethod
    def has_active_run(db: Session, incident_id: uuid.UUID) -> bool:
        return db.scalar(
            select(InvestigationRun.id).where(
                InvestigationRun.incident_id == incident_id,
                InvestigationRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        ) is not None

    # ── Worker side ─────────────────────────────────────────────────────────

    @staticmethod
    def start_run(db: Session, run_id: uuid.UUID) -> RunContext | None:
        """Marks a run RUNNING. Returns None if the run is gone or already finished (duplicate delivery)."""
        run = db.execute(
            select(InvestigationRun).where(InvestigationRun.id == run_id).with_for_update()
        ).scalar_one_or_none()
        if run is None:
            logger.warning("Investigation run %s not found; skipping", run_id)
            return None
        if run.status not in ACTIVE_RUN_STATUSES:
            logger.info("Investigation run %s already %s; skipping duplicate delivery", run_id, run.status.value)
            return None

        incident = db.get(Incident, run.incident_id)
        if incident is None:
            logger.warning("Incident for run %s no longer exists; skipping", run_id)
            return None

        # A run already RUNNING means the previous worker died — resume from the checkpoint
        resume = run.status == InvestigationRunStatus.RUNNING
        run.status = InvestigationRunStatus.RUNNING
        run.started_at = run.started_at or datetime.now(timezone.utc)
        db.commit()

        return RunContext(
            run_id=run.id,
            thread_id=incident.langgraph_thread_id,
            resume=resume,
            initial_state={
                "incident_id": str(incident.id),
                "run_id": str(run.id),
                "org_id": str(incident.org_id),
                "environment": incident.environment,
                "service": incident.service,
                "start_time": incident.start_time.isoformat(),
                "end_time": incident.end_time.isoformat(),
                "error_hint": incident.error_hint,
                "correlation_id": incident.correlation_id,
                "investigation_plan": [],
                "evidence_ids": [],
                "correlations": [],
                "gaps": [],
            },
        )

    @staticmethod
    def enter_node(db: Session, run_id: uuid.UUID, node_name: str, incident_state: IncidentState) -> None:
        """Records node progress on the run and moves the incident to the node's state."""
        run = db.get(InvestigationRun, run_id)
        if run is None:
            raise NotFoundError(f"Investigation run '{run_id}' not found")
        incident = db.get(Incident, run.incident_id)
        run.current_node = node_name
        record_transition(db, incident, incident_state, run_id=run.id, reason=f"Entered node '{node_name}'")
        db.commit()

    @staticmethod
    def complete_run(db: Session, run_id: uuid.UUID, outcome_state: IncidentState, reason: str | None) -> None:
        run = db.get(InvestigationRun, run_id)
        if run is None:
            return
        incident = db.get(Incident, run.incident_id)
        run.status = InvestigationRunStatus.COMPLETED
        run.outcome_state = outcome_state
        run.current_node = None
        run.completed_at = datetime.now(timezone.utc)
        record_transition(db, incident, outcome_state, run_id=run.id, reason=reason or "Investigation completed")
        db.commit()
        logger.info("Investigation run %s completed with outcome %s", run_id, outcome_state.value)

    @staticmethod
    def fail_run(db: Session, run_id: uuid.UUID, error: str) -> None:
        run = db.get(InvestigationRun, run_id)
        if run is None:
            return
        incident = db.get(Incident, run.incident_id)
        run.status = InvestigationRunStatus.FAILED
        run.outcome_state = IncidentState.INVESTIGATION_FAILED
        run.error = error[:MAX_ERROR_LENGTH]
        run.completed_at = datetime.now(timezone.utc)
        record_transition(db, incident, IncidentState.INVESTIGATION_FAILED, run_id=run.id, reason=error)
        db.commit()
        logger.error("Investigation run %s failed: %s", run_id, error)
