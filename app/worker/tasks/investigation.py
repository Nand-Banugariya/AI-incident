"""Celery task executing one LangGraph investigation run."""

import logging
import uuid
from celery.exceptions import SoftTimeLimitExceeded

import app.models  # noqa: F401  (register all mappers in the worker process)
from app.agents.checkpointer import get_checkpointer
from app.agents.guards.cost import CostBudgetExceeded
from app.agents.graph import build_investigation_graph
from app.agents.nodes.base import NodeExecutionError
from app.core.database import SessionLocal
from app.models.incident import IncidentState
from app.services.investigation_service import InvestigationService
from app.worker.celery_app import INVESTIGATION_TASK_NAME, celery_app

logger = logging.getLogger(__name__)


def _public_error(exc: Exception, run_id: uuid.UUID) -> str:
    """Client-safe failure summary: raw exception text can leak SQL, hosts or paths."""
    original = exc.original if isinstance(exc, NodeExecutionError) else exc
    if isinstance(original, CostBudgetExceeded):
        return str(original)
    where = f" in node '{exc.node_name}'" if isinstance(exc, NodeExecutionError) else ""
    return f"Unexpected {type(original).__name__}{where}; see worker logs for run {run_id}"


@celery_app.task(name=INVESTIGATION_TASK_NAME)
def run_investigation(run_id: str) -> None:
    """Runs (or resumes) the investigation graph. Never raises: every outcome is persisted on the run."""
    rid = uuid.UUID(run_id)

    with SessionLocal() as db:
        ctx = InvestigationService.start_run(db, rid)
    if ctx is None:
        return

    config = {"configurable": {"thread_id": ctx.thread_id}, "metadata": {"investigation_run_id": run_id}}

    try:
        with get_checkpointer() as checkpointer:
            graph = build_investigation_graph(checkpointer)
            graph_input = ctx.initial_state
            if ctx.resume and graph.get_state(config).next:
                logger.info("Resuming investigation run %s from last checkpoint", rid)
                graph_input = None
            final_state = graph.invoke(graph_input, config)

        outcome = IncidentState(final_state["outcome_state"])
        with SessionLocal() as db:
            InvestigationService.complete_run(db, rid, outcome, final_state.get("outcome_reason"))

    except SoftTimeLimitExceeded:
        with SessionLocal() as db:
            InvestigationService.fail_run(db, rid, "Investigation exceeded the configured time limit")
    except Exception as exc:
        logger.exception("Investigation run %s crashed", rid)
        with SessionLocal() as db:
            InvestigationService.fail_run(db, rid, _public_error(exc, rid))
