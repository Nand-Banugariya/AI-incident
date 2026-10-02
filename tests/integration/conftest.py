"""Fixtures for evidence, tool-call and run records used by the block-C tests."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.agent_run import AgentToolCall
from app.models.data_source import DataSource
from app.models.evidence import EvidenceType
from app.models.incident import Incident
from app.models.investigation import InvestigationRun, InvestigationRunStatus
from app.services.evidence_service import EvidenceService

T0 = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)  # inside the default incident window


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
def make_evidence(db):
    """Stores evidence through the real service (so hashing/dedup behave as in production)."""

    def _make(incident_id, source_id, *, content="ERROR boom", evidence_type=EvidenceType.APPLICATION_LOG,
              timestamp=None, **extra):
        incident = db.get(Incident, uuid.UUID(str(incident_id)))
        source = db.get(DataSource, uuid.UUID(str(source_id)))
        row, _ = EvidenceService.record(
            db, incident=incident, source=source, evidence_type=evidence_type,
            timestamp=timestamp or at(5), content=content, **extra,
        )
        db.commit()
        return row

    return _make


@pytest.fixture
def make_tool_call(db):
    def _make(incident_id, *, started_at=None, success=True, is_write=False, tool="query_logs",
              server="loki-mcp", node="collect_evidence", error=None, run_id=None, input_json=None):
        started_at = started_at or at(6)
        call = AgentToolCall(
            incident_id=uuid.UUID(str(incident_id)), run_id=run_id, agent_node=node, mcp_server=server,
            tool_name=tool, input_json=input_json or {"query": "secret-looking input"},
            started_at=started_at, completed_at=started_at + timedelta(milliseconds=40), duration_ms=40,
            success=success, error=error, is_write_operation=is_write,
        )
        db.add(call)
        db.commit()
        return call

    return _make


@pytest.fixture
def make_run(db):
    def _make(incident_id, triggered_by, *, status=InvestigationRunStatus.COMPLETED, created_at=None):
        run = InvestigationRun(
            incident_id=uuid.UUID(str(incident_id)), triggered_by=triggered_by, status=status,
            created_at=created_at or at(0),
        )
        db.add(run)
        db.commit()
        return run

    return _make


@pytest.fixture
def source(admin, create_data_source) -> dict:
    return create_data_source(admin, name="app-logs", provider_type="loki", capabilities=["application_logs"])


@pytest.fixture
def incident(admin, create_incident) -> dict:
    return create_incident(admin)
