"""GET /incidents/{id}/status and /timeline (C2)."""

import uuid
from datetime import timedelta

import pytest

from app.models.approval import ApprovalStage, ApprovalStatus
from app.models.incident import Incident, IncidentState, RiskLevel
from app.models.investigation import InvestigationRunStatus
from tests.integration.conftest import T0, at

FAR_FUTURE = T0 + timedelta(days=3650)  # sorts after real "now" timestamps written by the app


def status_of(client, incident_id, actor):
    response = client.get(f"/api/incidents/{incident_id}/status", headers=actor.headers)
    assert response.status_code == 200, response.text
    return response.json()


def timeline_of(client, incident_id, actor, **params):
    response = client.get(f"/api/incidents/{incident_id}/timeline", params=params, headers=actor.headers)
    assert response.status_code == 200, response.text
    return response.json()


# ── status ──────────────────────────────────────────────────────────────────


def test_status_of_a_new_incident(client, admin, incident):
    body = status_of(client, incident["id"], admin)
    assert body["incident_id"] == incident["id"]
    assert body["state"] == "created"
    assert body["risk_level"] is None and body["awaiting_approval"] is None
    assert body["evidence_count"] == 0 and body["latest_run"] is None
    assert float(body["total_llm_cost_usd"]) == 0


def test_status_after_triggering_an_investigation(client, admin, incident, task_queue):
    run = client.post(f"/api/incidents/{incident['id']}/investigate", headers=admin.headers).json()["run"]
    body = status_of(client, incident["id"], admin)
    assert body["state"] == "planning"
    assert (body["latest_run"]["id"], body["latest_run"]["status"]) == (run["id"], "queued")


def test_status_reports_the_newest_run(client, admin, incident, make_run):
    make_run(incident["id"], admin.user_id, status=InvestigationRunStatus.FAILED, created_at=at(0))
    newest = make_run(incident["id"], admin.user_id, status=InvestigationRunStatus.COMPLETED, created_at=at(30))
    make_run(incident["id"], admin.user_id, status=InvestigationRunStatus.FAILED, created_at=at(10))
    assert status_of(client, incident["id"], admin)["latest_run"]["id"] == str(newest.id)


def test_status_counts_evidence(client, admin, incident, source, make_evidence):
    for i in range(3):
        make_evidence(incident["id"], source["id"], content=f"line {i}", timestamp=at(i))
    assert status_of(client, incident["id"], admin)["evidence_count"] == 3


@pytest.mark.parametrize(
    "state,stage",
    [
        (IncidentState.WAITING_PLAN_APPROVAL, "plan"),
        (IncidentState.WAITING_CODE_APPROVAL, "code"),
        (IncidentState.WAITING_PRODUCTION_APPROVAL, "production"),
        (IncidentState.IMPLEMENTING, None),
        (IncidentState.RESOLVED, None),
    ],
)
def test_status_says_which_gate_the_incident_waits_at(client, db, admin, incident, state, stage):
    db.get(Incident, uuid.UUID(incident["id"])).state = state
    db.commit()
    assert status_of(client, incident["id"], admin)["awaiting_approval"] == stage


def test_status_exposes_risk_and_cost(client, db, admin, incident):
    row = db.get(Incident, uuid.UUID(incident["id"]))
    row.risk_level, row.total_llm_cost_usd = RiskLevel.HIGH, "1.2500"
    db.commit()
    body = status_of(client, incident["id"], admin)
    assert body["risk_level"] == "high" and float(body["total_llm_cost_usd"]) == 1.25


def test_status_access(client, viewer, other_admin, incident):
    assert status_of(client, incident["id"], viewer)["state"] == "created"
    assert client.get(f"/api/incidents/{incident['id']}/status").status_code == 401
    assert client.get(f"/api/incidents/{incident['id']}/status", headers=other_admin.headers).status_code == 404
    assert client.get(f"/api/incidents/{uuid.uuid4()}/status", headers=viewer.headers).status_code == 404
    assert client.get("/api/incidents/not-a-uuid/status", headers=viewer.headers).status_code == 422


# ── timeline ────────────────────────────────────────────────────────────────


def test_timeline_of_a_new_incident_is_its_creation(client, admin, incident):
    [event] = timeline_of(client, incident["id"], admin)
    assert event["kind"] == "state_transition"
    assert event["summary"] == "start -> created"
    assert event["actor_id"] == str(admin.user_id)
    assert event["details"]["to_state"] == "created" and event["details"]["from_state"] is None
    assert event["id"].startswith("state_transition:")


def test_timeline_records_the_investigation_trigger(client, admin, incident, task_queue):
    client.post(f"/api/incidents/{incident['id']}/investigate", headers=admin.headers)
    events = timeline_of(client, incident["id"], admin)
    assert [e["summary"] for e in events] == ["start -> created", "created -> planning"]
    assert events[1]["details"]["run_id"] is not None and events[1]["details"]["reason"] == "Investigation triggered"


def test_timeline_includes_approvals_requested_and_decided(client, admin, approver, incident, make_approval, db):
    make_approval(incident["id"], ApprovalStage.PLAN, ApprovalStatus.REJECTED, decided_by=approver.user_id, reason="no tests")
    make_approval(incident["id"], ApprovalStage.PLAN, ApprovalStatus.PENDING)
    events = {e["kind"]: e for e in timeline_of(client, incident["id"], admin) if e["kind"].startswith("approval")}

    decision = events["approval_decision"]
    assert decision["summary"] == "plan rejected"
    assert decision["actor_id"] == str(approver.user_id)
    assert decision["details"]["reason"] == "no tests" and decision["details"]["stage"] == "plan"
    requested = events["approval_requested"]
    assert requested["summary"] == "plan approval requested" and requested["actor_id"] is None


def test_timeline_includes_tool_calls_without_their_inputs(client, admin, incident, make_tool_call):
    make_tool_call(incident["id"], started_at=FAR_FUTURE, success=False, error="timeout", tool="query_logs")
    make_tool_call(incident["id"], started_at=FAR_FUTURE + timedelta(seconds=1), is_write=True, tool="apply_change")
    ok, write = [e for e in timeline_of(client, incident["id"], admin) if e["kind"] == "tool_call"]

    assert ok["summary"] == "loki-mcp.query_logs failed"
    assert ok["details"]["error"] == "timeout" and ok["details"]["success"] is False
    assert write["details"]["is_write_operation"] is True
    for event in (ok, write):
        assert "input_json" not in event["details"]
        assert "secret-looking input" not in str(event)  # tool inputs may carry sensitive queries


def test_timeline_is_oldest_first_across_all_kinds(client, admin, incident, make_approval, make_tool_call):
    make_approval(incident["id"], ApprovalStage.PLAN, ApprovalStatus.APPROVED)  # 2026-10-01 09:xx
    make_tool_call(incident["id"], started_at=FAR_FUTURE)
    events = timeline_of(client, incident["id"], admin)
    stamps = [e["timestamp"] for e in events]
    assert stamps == sorted(stamps)
    assert {e["kind"] for e in events} == {"state_transition", "approval_decision", "tool_call"}
    assert events[-1]["kind"] == "tool_call"


def test_timeline_ids_are_unique(client, admin, incident, make_tool_call):
    for i in range(5):
        make_tool_call(incident["id"], started_at=FAR_FUTURE + timedelta(seconds=i))
    ids = [e["id"] for e in timeline_of(client, incident["id"], admin)]
    assert len(ids) == len(set(ids)) == 6


def test_timeline_pagination_slices_the_merged_list(client, admin, incident, make_approval, make_tool_call):
    make_approval(incident["id"], ApprovalStage.PLAN, ApprovalStatus.APPROVED)
    for i in range(6):
        make_tool_call(incident["id"], started_at=FAR_FUTURE + timedelta(seconds=i))
    full = timeline_of(client, incident["id"], admin)
    assert len(full) == 8

    assert timeline_of(client, incident["id"], admin, limit=3) == full[:3]
    assert timeline_of(client, incident["id"], admin, limit=3, offset=3) == full[3:6]
    assert timeline_of(client, incident["id"], admin, limit=5, offset=6) == full[6:]
    assert timeline_of(client, incident["id"], admin, offset=50) == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}, {"offset": -1}])
def test_timeline_bad_params_are_422(client, admin, incident, params):
    assert client.get(f"/api/incidents/{incident['id']}/timeline", params=params, headers=admin.headers).status_code == 422


def test_timeline_only_covers_this_incident(client, admin, create_incident, incident, make_tool_call):
    other = create_incident(admin)
    make_tool_call(other["id"], started_at=FAR_FUTURE)
    assert [e["kind"] for e in timeline_of(client, incident["id"], admin)] == ["state_transition"]


def test_timeline_access(client, viewer, other_admin, incident):
    assert len(timeline_of(client, incident["id"], viewer)) == 1
    assert client.get(f"/api/incidents/{incident['id']}/timeline").status_code == 401
    assert client.get(f"/api/incidents/{incident['id']}/timeline", headers=other_admin.headers).status_code == 404
    assert client.get(f"/api/incidents/{uuid.uuid4()}/timeline", headers=viewer.headers).status_code == 404
