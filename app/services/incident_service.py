"""Service layer handling Incident business logic and persistence."""

import uuid
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentState
from app.models.user import User
from app.schemas.incident import IncidentCreate


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
        db.commit()
        db.refresh(incident)
        return incident
