"""Segregation of duties (B4): the creator of an incident cannot approve a high-risk change to it.

The rule applies to every stage, treats an unassessed risk as high (fails closed), and only restricts
approving: rejecting or revoking your own incident's change is always allowed.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.approval import Approval, ApprovalStage
from app.models.incident import Incident, IncidentState, RiskLevel
from app.services.approval_service import requires_independent_approver

STAGES = list(ApprovalStage)
GATE_STATE = {
    ApprovalStage.PLAN: IncidentState.WAITING_PLAN_APPROVAL,
    ApprovalStage.CODE: IncidentState.WAITING_CODE_APPROVAL,
    ApprovalStage.PRODUCTION: IncidentState.WAITING_PRODUCTION_APPROVAL,
}
RESTRICTED = [RiskLevel.HIGH, None]  # None: not yet assessed -> treated as high
UNRESTRICTED = [RiskLevel.LOW, RiskLevel.MEDIUM]


@pytest.fixture
def make_incident(db, create_incident):
    """An incident created by `creator`, parked at the stage's gate with the given risk level."""

    def _make(creator, stage=ApprovalStage.PLAN, risk=RiskLevel.HIGH) -> str:
        incident_id = create_incident(creator)["id"]
        incident = db.get(Incident, uuid.UUID(incident_id))
        incident.state, incident.risk_level = GATE_STATE[stage], risk
        db.commit()
        return incident_id

    return _make


def approve(client, incident_id, actor, stage=ApprovalStage.PLAN):
    return client.post(f"/api/incidents/{incident_id}/approve-{stage.value}", headers=actor.headers)


def reject(client, incident_id, actor, stage=ApprovalStage.PLAN, reason="not safe"):
    return client.post(
        f"/api/incidents/{incident_id}/reject-{stage.value}", json={"reason": reason}, headers=actor.headers
    )


def row_count(db) -> int:
    return db.scalar(select(func.count()).select_from(Approval))


# ── the rule itself ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "risk,restricted", [(RiskLevel.HIGH, True), (None, True), (RiskLevel.MEDIUM, False), (RiskLevel.LOW, False)]
)
def test_rule_table(risk, restricted):
    assert requires_independent_approver(risk) is restricted


# ── blocked: creator approving a high-risk / unassessed change ──────────────


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("risk", RESTRICTED)
def test_creator_cannot_approve_their_own_restricted_change(client, db, make_incident, approver, stage, risk):
    incident_id = make_incident(approver, stage, risk)  # created by the approver
    response = approve(client, incident_id, approver, stage)

    assert response.status_code == 403
    assert "cannot be approved by the user who created the incident" in response.json()["detail"]
    assert row_count(db) == 0  # nothing was recorded


def test_the_message_names_the_risk(client, make_incident, approver):
    assert "high-risk" in approve(client, make_incident(approver, risk=RiskLevel.HIGH), approver).json()["detail"]
    assert "unassessed-risk" in approve(client, make_incident(approver, risk=None), approver).json()["detail"]


def test_admins_are_not_exempt(client, make_incident, admin):
    incident_id = make_incident(admin, risk=RiskLevel.HIGH)
    assert approve(client, incident_id, admin).status_code == 403


def test_blocked_attempt_does_not_open_the_gate(client, db, make_incident, approver, admin):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import require_approval

    gated = FastAPI()

    @gated.post("/incidents/{incident_id}/go")
    def go(_: Approval = Depends(require_approval(ApprovalStage.PLAN))) -> dict:
        return {"ok": True}

    incident_id = make_incident(approver, risk=RiskLevel.HIGH)
    approve(client, incident_id, approver)
    assert TestClient(gated).post(f"/incidents/{incident_id}/go", headers=approver.headers).status_code == 403


def test_creator_stays_blocked_on_reapproval_after_a_rejection(client, make_incident, approver, admin):
    incident_id = make_incident(approver, risk=RiskLevel.HIGH)
    assert reject(client, incident_id, admin).status_code == 200
    assert approve(client, incident_id, approver).status_code == 403


# ── allowed: someone else approves ──────────────────────────────────────────


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("risk", RESTRICTED)
def test_a_different_approver_may_approve(client, make_incident, approver, admin, stage, risk):
    incident_id = make_incident(approver, stage, risk)
    response = approve(client, incident_id, admin, stage)
    assert response.status_code == 200
    assert response.json()["decided_by"] == str(admin.user_id)


def test_approver_can_approve_an_admins_high_risk_incident(client, make_incident, approver, admin):
    incident_id = make_incident(admin, risk=RiskLevel.HIGH)
    assert approve(client, incident_id, approver).status_code == 200


def test_an_approver_in_another_org_is_still_a_404_not_an_approval(client, make_incident, approver, other_admin):
    incident_id = make_incident(approver, risk=RiskLevel.HIGH)
    assert approve(client, incident_id, other_admin).status_code == 404


# ── allowed: creator on lower-risk changes ──────────────────────────────────


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("risk", UNRESTRICTED)
def test_creator_may_approve_low_and_medium_risk(client, make_incident, approver, stage, risk):
    incident_id = make_incident(approver, stage, risk)
    assert approve(client, incident_id, approver, stage).status_code == 200


# ── never restricted: rejecting / revoking ──────────────────────────────────


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("risk", RESTRICTED + UNRESTRICTED)
def test_creator_may_always_reject(client, make_incident, approver, stage, risk):
    incident_id = make_incident(approver, stage, risk)
    assert reject(client, incident_id, approver, stage).status_code == 200


def test_creator_may_revoke_an_approval_someone_else_gave(client, make_incident, approver, admin):
    incident_id = make_incident(approver, risk=RiskLevel.HIGH)
    assert approve(client, incident_id, admin).status_code == 200
    assert reject(client, incident_id, approver, reason="changed my mind").status_code == 200


# ── audit snapshot ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("risk", [RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW, None])
def test_decisions_snapshot_the_risk_level(client, make_incident, approver, admin, risk):
    incident_id = make_incident(approver, risk=risk)
    approved = approve(client, incident_id, admin).json()
    rejected = reject(client, incident_id, admin).json()
    expected = risk.value if risk else None
    assert approved["risk_level"] == rejected["risk_level"] == expected


def test_snapshot_keeps_the_risk_at_decision_time(client, db, make_incident, approver, admin):
    incident_id = make_incident(approver, risk=RiskLevel.LOW)
    approved = approve(client, incident_id, approver).json()  # creator may approve while it is low risk

    incident = db.get(Incident, uuid.UUID(incident_id))
    incident.risk_level = RiskLevel.HIGH  # e.g. the plan was regenerated and re-assessed
    db.commit()

    history = client.get(f"/api/incidents/{incident_id}/approvals", headers=admin.headers).json()
    assert history[0]["id"] == approved["id"]
    assert history[0]["risk_level"] == "low"  # the record of what was decided under which risk


def test_the_rule_uses_the_current_risk_not_a_past_one(client, db, make_incident, approver, admin):
    incident_id = make_incident(approver, risk=RiskLevel.LOW)
    approve(client, incident_id, approver)  # allowed at low risk
    reject(client, incident_id, admin)

    incident = db.get(Incident, uuid.UUID(incident_id))
    incident.risk_level = RiskLevel.HIGH
    db.commit()
    assert approve(client, incident_id, approver).status_code == 403  # now restricted


# ── exposure ────────────────────────────────────────────────────────────────


def test_incident_exposes_risk_level(client, admin, create_incident):
    created = create_incident(admin)
    assert created["risk_level"] is None
    assert client.get(f"/api/incidents/{created['id']}", headers=admin.headers).json()["risk_level"] is None


def test_risk_level_cannot_be_set_through_the_api(client, admin):
    """Risk comes from the planner; a client must not be able to downgrade it to dodge the rule."""
    from tests.factories import incident_payload

    response = client.post("/api/incidents", json={**incident_payload(), "risk_level": "low"}, headers=admin.headers)
    assert response.status_code == 201
    assert response.json()["risk_level"] is None
