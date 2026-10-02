"""Approval table integrity (B1): the DB itself refuses states that would weaken the audit trail."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.approval import Approval, ApprovalStage, ApprovalStatus

PLAN, CODE = ApprovalStage.PLAN, ApprovalStage.CODE
PENDING, APPROVED, REJECTED = ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED


@pytest.fixture
def incident_id(admin, create_incident) -> uuid.UUID:
    return uuid.UUID(create_incident(admin)["id"])


def test_new_approval_defaults_to_pending_with_timestamp(db, incident_id):
    approval = Approval(incident_id=incident_id, stage=PLAN)
    db.add(approval)
    db.commit()
    db.refresh(approval)
    assert approval.status == PENDING
    assert approval.created_at is not None
    assert (approval.decided_by, approval.decided_at, approval.reason) == (None, None, None)


def test_only_one_pending_request_per_incident_and_stage(db, incident_id):
    db.add(Approval(incident_id=incident_id, stage=PLAN))
    db.commit()
    db.add(Approval(incident_id=incident_id, stage=PLAN))
    with pytest.raises(IntegrityError):
        db.commit()


def test_pending_requests_for_other_stages_or_incidents_are_independent(db, admin, create_incident, incident_id):
    other_incident = uuid.UUID(create_incident(admin)["id"])
    db.add_all(
        [
            Approval(incident_id=incident_id, stage=PLAN),
            Approval(incident_id=incident_id, stage=CODE),
            Approval(incident_id=other_incident, stage=PLAN),
        ]
    )
    db.commit()


def test_history_is_preserved_across_decisions(db, incident_id, make_approval):
    make_approval(incident_id, PLAN, REJECTED, reason="needs a rollback plan")
    make_approval(incident_id, PLAN, REJECTED, reason="still missing tests")
    make_approval(incident_id, PLAN, APPROVED)
    make_approval(incident_id, PLAN, PENDING)  # a new request may open once earlier ones are decided
    assert db.query(Approval).filter_by(incident_id=incident_id, stage=PLAN).count() == 4


@pytest.mark.parametrize("reason", [None, ""])
def test_rejection_requires_a_reason(db, incident_id, reason):
    db.add(
        Approval(
            incident_id=incident_id, stage=PLAN, status=REJECTED, reason=reason,
            decided_by=uuid.uuid4(), decided_at=datetime.now(timezone.utc),  # only the reason is missing
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.parametrize("status", [APPROVED, REJECTED])
def test_a_decision_must_record_who_and_when(db, incident_id, status):
    db.add(Approval(incident_id=incident_id, stage=PLAN, status=status, reason="because"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_decision_with_actor_and_time_is_accepted(db, incident_id, make_approval):
    approval = make_approval(incident_id, PLAN, APPROVED)
    assert approval.status == APPROVED
    assert approval.decided_by is not None and approval.decided_at is not None
