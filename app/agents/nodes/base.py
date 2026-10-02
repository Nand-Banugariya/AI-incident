"""Shared node wrapper: records progress/state per node and tags failures with the node name (§6)."""

import functools
import logging
import uuid
from typing import Callable

import structlog

from app.agents.state import InvestigationState
from app.core.database import SessionLocal
from app.models.incident import IncidentState
from app.services.investigation_service import InvestigationService

logger = logging.getLogger(__name__)

NodeFn = Callable[[InvestigationState], dict]


class NodeExecutionError(Exception):
    """Wraps an unexpected exception so the worker can record which node failed."""

    def __init__(self, node_name: str, original: Exception):
        self.node_name = node_name
        self.original = original
        super().__init__(f"{type(original).__name__} in node '{node_name}': {original}")


def investigation_node(name: str, incident_state: IncidentState) -> Callable[[NodeFn], NodeFn]:
    """Decorator: moves the incident into `incident_state` before the node body runs."""
    def decorator(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        def wrapper(state: InvestigationState) -> dict:
            run_id = uuid.UUID(state["run_id"])
            structlog.contextvars.bind_contextvars(
                incident_id=state["incident_id"], run_id=state["run_id"], node_name=name
            )
            with SessionLocal() as db:
                InvestigationService.enter_node(db, run_id, name, incident_state)
            logger.info("Run %s entering node '%s' (incident %s)", run_id, name, state["incident_id"])
            try:
                return fn(state)
            except NodeExecutionError:
                raise
            except Exception as exc:
                raise NodeExecutionError(name, exc) from exc
            finally:
                structlog.contextvars.clear_contextvars()
        return wrapper
    return decorator
