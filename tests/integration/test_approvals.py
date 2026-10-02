"""Approve/reject endpoints (B3): who may decide, when, and what ends up in the audit trail."""

import uuid

import pytest
from sqlalchemy import func, select

from app.api.deps import require_approval
from app.models.approval import Approval, ApprovalStage, ApprovalStatus
from app.models.incident import Incident, IncidentState, RiskLevel

PLAN, CODE, PRODUCTION = ApprovalStage.PLAN, ApprovalStage.CODE, ApprovalStage.PRODUCTION
PENDING, APPROVED, REJECTED = ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED

GATE_STATE = {
    PLAN: IncidentState.WAITING_PLAN_APPROVAL,
    CODE: IncidentState.WAITING_CODE_APPROVAL,
    PRODUCTION: IncidentState.WAITING_PRODUCTION_APPROVAL,
}
STAGES = list(ApprovalStage)


@pytest.fixture
def at_gate(db, admin, create_incident):
    """Creates an incident parked at the given stage's gate (the graph does this once it exists).

    Defaults to low risk so these tests are not about segregation of duties (see test_segregation_of_duties.py).
    """

    def _make(stage: ApprovalStage = PLAN, risk: RiskLevel | None = RiskLevel.LOW) -> str:
        incident_id = create_incident(admin)["id"]
        incident = db.get(Incident, uuid.UUID(incident_id))
        incident.state = GATE_STATE[stage]
        incident.risk_level = risk
        db.commit()
        return incident_id

    return _make


def approve(client, incident_id, actor, stage=PLAN, **json):
    return client.post(f"/api/incidents/{incident_id}/approve-{stage.value}", json=json or None, headers=actor.headers)


def reject(client, incident_id, actor, stage=PLAN, reason="not safe"):
    return client.post(
        f"/api/incidents/{incident_id}/reject-{stage.value}", json={"reason": reason}, headers=actor.headers
    )


def history(client, incident_id, actor, **params):
    response = client.get(f"/api/incidents/{incident_id}/approvals", params=params, headers=actor.headers)
    assert response.status_code == 200, response.text
    return response.json()


# ── happy paths, for every stage ────────────────────────────────────────────


@pytest.mark.parametrize("stage", STAGES)
def test_approve_records_who_and_when(client, at_gate, approver, stage):
    incident_id = at_gate(stage)
    response = approve(client, incident_id, approver, stage)

    assert response.status_code == 200
    body = response.json()
    assert (body["stage"], body["status"]) == (stage.value, "approved")
    assert body["decided_by"] == str(approver.user_id)
    assert body["decided_at"] is not None
    assert body["incident_id"] == incident_id


@pytest.mark.parametrize("stage", STAGES)
def test_reject_records_reason(client, at_gate, approver, stage):
    incident_id = at_gate(stage)
    response = reject(client, incident_id, approver, stage, reason="  missing rollback  ")

    assert response.status_code == 200
    body = response.json()
    assert (body["stage"], body["status"], body["reason"]) == (stage.value, "rejected", "missing rollback")
    assert body["decided_by"] == str(approver.user_id)


def test_approval_may_carry_an_optional_note(client, at_gate, admin):
    incident_id = at_gate()
    assert approve(client, incident_id, admin, reason="  checked with the DBA ").json()["reason"] == "checked with the DBA"
    other = at_gate()
    assert approve(client, other, admin, reason="   ").json()["reason"] is None  # blank note is dropped


def test_approve_works_with_no_body_at_all(client, at_gate, admin):
    incident_id = at_gate()
    response = client.post(f"/api/incidents/{incident_id}/approve-plan", headers=admin.headers)
    assert response.status_code == 200


def test_decisions_do_not_change_incident_state(client, at_gate, admin, db):
    incident_id = at_gate()
    approve(client, incident_id, admin)
    reject(client, incident_id, admin)
    db.expire_all()
    assert db.get(Incident, uuid.UUID(incident_id)).state == IncidentState.WAITING_PLAN_APPROVAL


# ── validation ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [None, {}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 1001}])
def test_reject_requires_a_real_reason(client, at_gate, admin, body):
    incident_id = at_gate()
    response = client.post(f"/api/incidents/{incident_id}/reject-plan", json=body, headers=admin.headers)
    assert response.status_code == 422


def test_invalid_reject_creates_no_row(client, at_gate, admin, db):
    incident_id = at_gate()
    client.post(f"/api/incidents/{incident_id}/reject-plan", json={"reason": " "}, headers=admin.headers)
    assert db.scalar(select(func.count()).select_from(Approval)) == 0


# ── state gating ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", STAGES)
def test_decision_requires_the_stages_gate_state(client, admin, create_incident, stage):
    incident_id = create_incident(admin)["id"]  # state: created
    assert approve(client, incident_id, admin, stage).status_code == 409
    assert reject(client, incident_id, admin, stage).status_code == 409


@pytest.mark.parametrize("gate_stage", STAGES)
@pytest.mark.parametrize("decided_stage", STAGES)
def test_a_gate_only_accepts_its_own_stage(client, at_gate, admin, gate_stage, decided_stage):
    incident_id = at_gate(gate_stage)
    expected = 200 if gate_stage == decided_stage else 409
    assert approve(client, incident_id, admin, decided_stage).status_code == expected


def test_rejected_attempts_leave_no_trace(client, admin, create_incident, db):
    incident_id = create_incident(admin)["id"]
    approve(client, incident_id, admin)
    assert db.scalar(select(func.count()).select_from(Approval)) == 0


# ── repeat, revoke and re-approve ───────────────────────────────────────────


def test_approving_twice_is_409(client, at_gate, admin):
    incident_id = at_gate()
    assert approve(client, incident_id, admin).status_code == 200
    assert approve(client, incident_id, admin).status_code == 409
    assert len(history(client, incident_id, admin)) == 1


def test_rejecting_twice_is_409(client, at_gate, admin):
    incident_id = at_gate()
    assert reject(client, incident_id, admin).status_code == 200
    assert reject(client, incident_id, admin).status_code == 409


def test_rejected_stage_can_be_reapproved_and_history_is_kept(client, at_gate, admin, approver):
    incident_id = at_gate()
    first = reject(client, incident_id, admin, reason="add tests").json()
    second = approve(client, incident_id, approver).json()

    rows = history(client, incident_id, admin)
    assert [r["status"] for r in rows] == ["approved", "rejected"]  # newest first
    assert second["id"] != first["id"]
    assert rows[1] == first  # the rejection row was not rewritten


def test_approval_can_be_revoked_by_a_later_rejection(client, at_gate, admin):
    incident_id = at_gate()
    approve(client, incident_id, admin)
    assert reject(client, incident_id, admin, reason="found a regression").status_code == 200
    assert [r["status"] for r in history(client, incident_id, admin)] == ["rejected", "approved"]


def test_decided_rows_are_never_edited(client, at_gate, admin, db):
    incident_id = at_gate()
    first = approve(client, incident_id, admin).json()
    reject(client, incident_id, admin)
    approve(client, incident_id, admin)

    db.expire_all()
    original = db.get(Approval, uuid.UUID(first["id"]))
    assert original.status == APPROVED and original.reason is None
    assert db.scalar(select(func.count()).select_from(Approval)) == 3


def test_an_open_request_is_answered_in_place(client, at_gate, admin, make_approval, db):
    incident_id = at_gate()
    pending = make_approval(incident_id, PLAN, PENDING)

    response = approve(client, incident_id, admin)
    assert response.json()["id"] == str(pending.id)  # same row, no duplicate
    assert db.scalar(select(func.count()).select_from(Approval)) == 1
    db.refresh(pending)
    assert pending.status == APPROVED and pending.decided_by == admin.user_id


def test_a_request_opened_after_an_approval_can_be_rejected_and_reapproved(client, at_gate, admin, make_approval):
    incident_id = at_gate()
    make_approval(incident_id, PLAN, APPROVED)
    make_approval(incident_id, PLAN, PENDING)  # e.g. the plan was regenerated
    assert reject(client, incident_id, admin).status_code == 200
    assert approve(client, incident_id, admin).status_code == 200


# ── the decisions drive the real gate ───────────────────────────────────────


def test_approve_then_revoke_opens_and_closes_require_approval(client, at_gate, admin, db):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    gated = FastAPI()

    @gated.post("/incidents/{incident_id}/go")
    def go(_: Approval = Depends(require_approval(PLAN))) -> dict:
        return {"ok": True}

    gate = TestClient(gated)
    incident_id = at_gate()
    url = f"/incidents/{incident_id}/go"

    assert gate.post(url, headers=admin.headers).status_code == 403
    approve(client, incident_id, admin)
    assert gate.post(url, headers=admin.headers).status_code == 200
    reject(client, incident_id, admin, reason="revoked")
    assert gate.post(url, headers=admin.headers).status_code == 403
    approve(client, incident_id, admin)
    assert gate.post(url, headers=admin.headers).status_code == 200


# ── access control ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", STAGES)
def test_viewer_cannot_decide(client, at_gate, viewer, db, stage):
    incident_id = at_gate(stage)
    assert approve(client, incident_id, viewer, stage).status_code == 403
    assert reject(client, incident_id, viewer, stage).status_code == 403
    assert db.scalar(select(func.count()).select_from(Approval)) == 0


@pytest.mark.parametrize("stage", STAGES)
def test_other_org_gets_404_and_nothing_is_recorded(client, at_gate, other_admin, db, stage):
    incident_id = at_gate(stage)
    assert approve(client, incident_id, other_admin, stage).status_code == 404
    assert reject(client, incident_id, other_admin, stage).status_code == 404
    assert db.scalar(select(func.count()).select_from(Approval)) == 0


def test_unknown_and_malformed_incident(client, admin):
    assert approve(client, uuid.uuid4(), admin).status_code == 404
    assert reject(client, uuid.uuid4(), admin).status_code == 404
    assert approve(client, "not-a-uuid", admin).status_code == 422


def test_decisions_require_authentication(client, at_gate):
    incident_id = at_gate()
    assert client.post(f"/api/incidents/{incident_id}/approve-plan").status_code == 401
    assert client.post(f"/api/incidents/{incident_id}/reject-plan", json={"reason": "x"}).status_code == 401


# ── history endpoint ────────────────────────────────────────────────────────


def test_history_is_empty_for_a_new_incident(client, admin, create_incident):
    assert history(client, create_incident(admin)["id"], admin) == []


def test_history_is_readable_by_viewers_and_filterable_by_stage(client, at_gate, admin, viewer, make_approval):
    incident_id = at_gate()
    make_approval(incident_id, PLAN, APPROVED)
    make_approval(incident_id, CODE, REJECTED)

    assert len(history(client, incident_id, viewer)) == 2
    assert [r["stage"] for r in history(client, incident_id, viewer, stage="code")] == ["code"]
    assert client.get(f"/api/incidents/{incident_id}/approvals?stage=bogus", headers=viewer.headers).status_code == 422


def test_history_is_org_scoped(client, at_gate, other_admin):
    incident_id = at_gate()
    assert client.get(f"/api/incidents/{incident_id}/approvals", headers=other_admin.headers).status_code == 404
    assert client.get(f"/api/incidents/{incident_id}/approvals").status_code == 401
