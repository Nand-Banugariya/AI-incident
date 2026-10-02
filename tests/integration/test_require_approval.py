"""require_approval() (B2): a write-triggering route opens only for an APPROVED row of the exact incident+stage.

Exercised through a tiny throwaway app so the dependency is tested on its own, before real gated
endpoints (/implement, /create-pr, ...) exist.
"""

import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_approval
from app.models.approval import Approval, ApprovalStage, ApprovalStatus

PLAN, CODE, PRODUCTION = ApprovalStage.PLAN, ApprovalStage.CODE, ApprovalStage.PRODUCTION
PENDING, APPROVED, REJECTED = ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED

gate_app = FastAPI()


@gate_app.post("/incidents/{incident_id}/plan-gated")
def plan_gated(approval: Approval = Depends(require_approval(PLAN))) -> dict:
    return {"approval_id": str(approval.id)}


@gate_app.post("/incidents/{incident_id}/code-gated")
def code_gated(approval: Approval = Depends(require_approval(CODE))) -> dict:
    return {"approval_id": str(approval.id)}


@gate_app.post("/incidents/{incident_id}/production-gated")
def production_gated(approval: Approval = Depends(require_approval(PRODUCTION))) -> dict:
    return {"approval_id": str(approval.id)}


@pytest.fixture
def gate() -> TestClient:
    return TestClient(gate_app)


@pytest.fixture
def incident_id(admin, create_incident) -> str:
    return create_incident(admin)["id"]


def call(gate, incident_id, actor, path="plan-gated"):
    return gate.post(f"/incidents/{incident_id}/{path}", headers=actor.headers)


def test_no_approval_row_is_denied(gate, admin, incident_id):
    response = call(gate, incident_id, admin)
    assert response.status_code == 403
    assert response.json()["detail"] == "No approved plan approval for this incident"


@pytest.mark.parametrize("status", [PENDING, REJECTED])
def test_pending_or_rejected_is_denied(gate, admin, incident_id, make_approval, status):
    make_approval(incident_id, PLAN, status)
    assert call(gate, incident_id, admin).status_code == 403


def test_approved_row_opens_the_gate_and_is_returned(gate, admin, incident_id, make_approval):
    approval = make_approval(incident_id, PLAN, APPROVED)
    response = call(gate, incident_id, admin)
    assert response.status_code == 200
    assert response.json() == {"approval_id": str(approval.id)}


def test_gate_does_not_check_roles_only_approval(gate, viewer, admin, incident_id, make_approval):
    """Role checks are require_permission's job; the two layers are independent."""
    make_approval(incident_id, PLAN, APPROVED)
    assert call(gate, incident_id, viewer).status_code == 200


# ── stage isolation: approving one stage never unlocks another ──────────────


@pytest.mark.parametrize(
    "approved_stage,gated_path",
    [
        (PLAN, "code-gated"),
        (PLAN, "production-gated"),
        (CODE, "plan-gated"),
        (CODE, "production-gated"),
        (PRODUCTION, "plan-gated"),
        (PRODUCTION, "code-gated"),
    ],
)
def test_approval_for_another_stage_does_not_open_the_gate(
    gate, admin, incident_id, make_approval, approved_stage, gated_path
):
    make_approval(incident_id, approved_stage, APPROVED)
    assert call(gate, incident_id, admin, gated_path).status_code == 403


def test_each_stage_gate_opens_with_its_own_approval(gate, admin, incident_id, make_approval):
    for stage, path in [(PLAN, "plan-gated"), (CODE, "code-gated"), (PRODUCTION, "production-gated")]:
        assert call(gate, incident_id, admin, path).status_code == 403
        make_approval(incident_id, stage, APPROVED)
        assert call(gate, incident_id, admin, path).status_code == 200


# ── incident isolation: an approval is for THAT incident only ───────────────


def test_approval_on_another_incident_does_not_open_the_gate(gate, admin, incident_id, create_incident, make_approval):
    other = create_incident(admin)["id"]
    make_approval(other, PLAN, APPROVED)
    assert call(gate, incident_id, admin).status_code == 403
    assert call(gate, other, admin).status_code == 200


# ── newest row wins (fails closed) ──────────────────────────────────────────


def test_later_rejection_revokes_an_earlier_approval(gate, admin, incident_id, make_approval):
    make_approval(incident_id, PLAN, APPROVED)
    assert call(gate, incident_id, admin).status_code == 200
    make_approval(incident_id, PLAN, REJECTED)
    assert call(gate, incident_id, admin).status_code == 403


def test_reapproval_after_rejection_reopens_the_gate(gate, admin, incident_id, make_approval):
    make_approval(incident_id, PLAN, REJECTED)
    approved = make_approval(incident_id, PLAN, APPROVED)
    response = call(gate, incident_id, admin)
    assert response.status_code == 200
    assert response.json()["approval_id"] == str(approved.id)


def test_newly_opened_request_revokes_an_earlier_approval(gate, admin, incident_id, make_approval):
    make_approval(incident_id, PLAN, APPROVED)
    make_approval(incident_id, PLAN, PENDING)
    assert call(gate, incident_id, admin).status_code == 403


# ── tenancy and authentication ──────────────────────────────────────────────


def test_other_orgs_incident_is_404_even_if_approved(gate, other_admin, incident_id, make_approval):
    make_approval(incident_id, PLAN, APPROVED)
    assert call(gate, incident_id, other_admin).status_code == 404


def test_unknown_incident_is_404(gate, admin):
    assert call(gate, uuid.uuid4(), admin).status_code == 404


def test_malformed_incident_id_is_422(gate, admin):
    assert call(gate, "not-a-uuid", admin).status_code == 422


def test_unauthenticated_is_401(gate, incident_id, make_approval):
    make_approval(incident_id, PLAN, APPROVED)
    assert gate.post(f"/incidents/{incident_id}/plan-gated").status_code == 401


def test_denial_is_logged_with_context(gate, admin, incident_id, caplog):
    with caplog.at_level("WARNING", logger="app.api.deps"):
        call(gate, incident_id, admin)
    assert any("Approval gate denied" in r.message and incident_id in r.message for r in caplog.records)
