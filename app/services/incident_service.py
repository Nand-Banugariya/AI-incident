"""Service layer handling Incident business logic and persistence."""

import logging
import uuid
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentState
from app.models.investigation import IncidentStateTransition
from app.models.user import User
from app.schemas.incident import IncidentCreate
from app.services.exceptions import ConflictError
from app.services.investigation_service import InvestigationService

logger = logging.getLogger(__name__)


class IncidentService:
    """Encapsulates incident lifecycle and database operations."""

    @staticmethod
    def create_incident(
        db: Session,
        payload: IncidentCreate,
        current_user: User,
    ) -> Incident:
        """Creates a new incident record scoped to the user's organization."""
        # Generate a dedicated, unique LangGraph thread ID per incident (§2)
        unique_thread_id = f"incident-thread-{uuid.uuid4()}"

        incident = Incident(
            org_id=current_user.org_id,
            environment=payload.environment.strip().lower(),
            service=payload.service.strip() if payload.service else None,
            start_time=payload.start_time,
            end_time=payload.end_time,
            error_hint=payload.error_hint,
            correlation_id=payload.correlation_id,
            state=IncidentState.CREATED,
            langgraph_thread_id=unique_thread_id,
            created_by=current_user.id,
            total_llm_cost_usd=Decimal("0.0000"),
        )

        db.add(incident)
        db.flush()
        db.add(
            IncidentStateTransition(
                incident_id=incident.id,
                from_state=None,
                to_state=IncidentState.CREATED,
                changed_by=current_user.id,
                reason="Incident created",
            )
        )
        db.commit()
        db.refresh(incident)
        return incident

    @staticmethod
    def delete_incident(
        db: Session,
        incident_id: uuid.UUID,
        current_user: User,
    ) -> bool:
        """Deletes an incident within the user's organization. Returns False if not found."""
        incident = db.execute(
            select(Incident).where(
                Incident.id == incident_id,
                Incident.org_id == current_user.org_id,
            )
        ).scalar_one_or_none()

        if not incident:
            return False

        if InvestigationService.has_active_run(db, incident.id):
            raise ConflictError("Cannot delete an incident while an investigation is in progress")

        db.delete(incident)
        db.commit()
        logger.info(
            "Incident %s deleted by user %s (org %s)",
            incident_id,
            current_user.id,
            current_user.org_id,
        )
        return True
