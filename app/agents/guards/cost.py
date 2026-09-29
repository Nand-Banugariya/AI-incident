"""Per-incident LLM cost circuit breaker (§6) — call at the top of every LLM-calling node."""

import uuid
from decimal import Decimal

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.incident import Incident


class CostBudgetExceeded(Exception):
    """Raised when an incident's running LLM spend reaches the configured limit."""


def check_cost_budget(incident_id: str) -> None:
    limit = Decimal(str(settings.LLM_MAX_COST_PER_INCIDENT_USD))
    with SessionLocal() as db:
        spent = db.get(Incident, uuid.UUID(incident_id)).total_llm_cost_usd
    if spent >= limit:
        raise CostBudgetExceeded(
            f"Incident {incident_id} has spent ${spent}, exceeding the ${limit} limit"
        )
