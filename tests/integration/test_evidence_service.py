"""Evidence storage (C1) and idempotent re-runs (C3): the same observation is never stored twice."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.agent_run import AgentToolCall
from app.models.data_source import DataSource
from app.models.evidence import Evidence, EvidenceType
from app.models.incident import Incident
from app.services.evidence_service import EvidenceService, compute_content_hash, record_many
from app.services.exceptions import ForbiddenError
from tests.integration.conftest import at

LOG = EvidenceType.APPLICATION_LOG
SRC = uuid.UUID("11111111-1111-1111-1111-111111111111")


def count(db) -> int:
    return db.scalar(select(func.count()).select_from(Evidence))


# ── the hash ────────────────────────────────────────────────────────────────


def test_hash_is_deterministic():
    kwargs = dict(source_id=SRC, evidence_type=LOG, timestamp=at(1), content="boom")
    assert compute_content_hash(**kwargs) == compute_content_hash(**kwargs)
    assert len(compute_content_hash(**kwargs)) == 64


@pytest.mark.parametrize(
    "change",
    [
        {"source_id": uuid.UUID("22222222-2222-2222-2222-222222222222")},
        {"evidence_type": EvidenceType.DATABASE_LOG},
        {"timestamp": at(2)},
        {"content": "boom!"},
    ],
    ids=["source", "type", "timestamp", "content"],
)
def test_hash_changes_with_every_identity_field(change):
    base = dict(source_id=SRC, evidence_type=LOG, timestamp=at(1), content="boom")
    assert compute_content_hash(**base) != compute_content_hash(**{**base, **change})


def test_hash_treats_equal_instants_as_equal_whatever_the_offset_or_awareness():
    base = dict(source_id=SRC, evidence_type=LOG, content="boom")
    utc = compute_content_hash(timestamp=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc), **base)
    plus_one = compute_content_hash(timestamp=datetime(2026, 10, 1, 10, 0, tzinfo=timezone(timedelta(hours=1))), **base)
    naive = compute_content_hash(timestamp=datetime(2026, 10, 1, 9, 0), **base)  # databases may hand back naive UTC
    assert utc == plus_one == naive


# ── recording ───────────────────────────────────────────────────────────────


def test_record_stores_all_fields(db, incident, source):
    row, created = EvidenceService.record(
        db, incident=db.get(Incident, uuid.UUID(incident["id"])), source=db.get(DataSource, uuid.UUID(source["id"])),
        evidence_type=LOG, timestamp=at(3), content="raw line", service="payments",
        normalized_content="normalised", correlation_id="req-1", severity="error",
    )
    db.commit()
    assert created is True
    stored = db.get(Evidence, row.id)
    assert (stored.content, stored.normalized_content, stored.service, stored.correlation_id, stored.severity) == (
        "raw line", "normalised", "payments", "req-1", "error"
    )
    assert stored.content_hash and stored.created_at is not None


def test_recording_the_same_evidence_twice_stores_it_once(db, incident, source, make_evidence):
    first = make_evidence(incident["id"], source["id"], content="same line")
    second = make_evidence(incident["id"], source["id"], content="same line")
    assert second.id == first.id
    assert count(db) == 1


def test_record_reports_whether_it_created_the_row(db, incident, source):
    inc, src = db.get(Incident, uuid.UUID(incident["id"])), db.get(DataSource, uuid.UUID(source["id"]))
    args = dict(incident=inc, source=src, evidence_type=LOG, timestamp=at(1), content="x")
    assert EvidenceService.record(db, **args)[1] is True
    assert EvidenceService.record(db, **args)[1] is False


def test_same_content_from_a_different_source_is_a_separate_observation(db, admin, incident, source, create_data_source, make_evidence):
    other_source = create_data_source(admin, name="second-logs", provider_type="loki")
    make_evidence(incident["id"], source["id"], content="same line")
    make_evidence(incident["id"], other_source["id"], content="same line")
    assert count(db) == 2


def test_same_evidence_on_different_incidents_is_stored_for_each(db, admin, create_incident, incident, source, make_evidence):
    other_incident = create_incident(admin)
    make_evidence(incident["id"], source["id"], content="same line")
    make_evidence(other_incident["id"], source["id"], content="same line")
    assert count(db) == 2


def test_unique_constraint_backs_up_the_service(db, incident, source, make_evidence):
    """Even a writer that bypasses the service cannot create a duplicate."""
    row = make_evidence(incident["id"], source["id"])
    db.add(
        Evidence(
            incident_id=row.incident_id, source_id=row.source_id, evidence_type=LOG, timestamp=at(9),
            content="other", content_hash=row.content_hash,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


# ── C3: re-running an investigation must not duplicate evidence ─────────────


def _batch(inc, src, contents):
    return [
        dict(incident=inc, source=src, evidence_type=LOG, timestamp=at(i), content=text)
        for i, text in enumerate(contents)
    ]


def test_rerun_with_identical_results_adds_nothing(db, incident, source):
    inc, src = db.get(Incident, uuid.UUID(incident["id"])), db.get(DataSource, uuid.UUID(source["id"]))
    batch = _batch(inc, src, ["a", "b", "c"])

    assert record_many(db, batch) == (3, 0)
    db.commit()
    assert record_many(db, batch) == (0, 3)  # the second /investigate finds the same logs
    db.commit()
    assert count(db) == 3


def test_rerun_with_overlapping_results_adds_only_the_new_rows(db, incident, source):
    inc, src = db.get(Incident, uuid.UUID(incident["id"])), db.get(DataSource, uuid.UUID(source["id"]))
    record_many(db, _batch(inc, src, ["a", "b"]))
    db.commit()
    created, skipped = record_many(db, _batch(inc, src, ["a", "b", "c", "d"]))
    db.commit()
    assert (created, skipped) == (2, 2)
    assert count(db) == 4


def test_existing_hashes_lets_a_rerun_skip_fetching(db, incident, source, make_evidence):
    a = make_evidence(incident["id"], source["id"], content="a")
    b = make_evidence(incident["id"], source["id"], content="b", timestamp=at(7))
    hashes = EvidenceService.existing_hashes(db, uuid.UUID(incident["id"]))
    assert hashes == {a.content_hash, b.content_hash}
    would_be = compute_content_hash(source_id=a.source_id, evidence_type=LOG, timestamp=at(5), content="a")
    assert would_be in hashes  # the collector can recognise it already has this before storing it
    assert EvidenceService.existing_hashes(db, uuid.uuid4()) == set()


def test_losing_a_race_returns_the_winner_and_keeps_the_transaction_usable(db, incident, source, monkeypatch):
    inc, src = db.get(Incident, uuid.UUID(incident["id"])), db.get(DataSource, uuid.UUID(source["id"]))
    args = dict(incident=inc, source=src, evidence_type=LOG, timestamp=at(1), content="raced")
    winner, _ = EvidenceService.record(db, **args)
    db.commit()

    real_find = EvidenceService._find
    calls = {"n": 0}

    def blind_first_time(*a, **k):  # our pre-insert check misses the row a concurrent run just wrote
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(*a, **k)

    monkeypatch.setattr(EvidenceService, "_find", staticmethod(blind_first_time))
    row, created = EvidenceService.record(db, **args)

    assert (row.id, created) == (winner.id, False)
    db.add(Evidence(  # the surrounding transaction still works after the failed insert
        incident_id=inc.id, source_id=src.id, evidence_type=LOG, timestamp=at(2), content="later",
        content_hash=compute_content_hash(source_id=src.id, evidence_type=LOG, timestamp=at(2), content="later"),
    ))
    db.commit()
    assert count(db) == 2


# ── tenancy ─────────────────────────────────────────────────────────────────


def test_a_source_from_another_org_cannot_feed_an_incident(db, incident, other_admin, create_data_source):
    foreign = create_data_source(other_admin, name="their-logs", provider_type="loki")
    with pytest.raises(ForbiddenError):
        EvidenceService.record(
            db, incident=db.get(Incident, uuid.UUID(incident["id"])),
            source=db.get(DataSource, uuid.UUID(foreign["id"])),
            evidence_type=LOG, timestamp=at(1), content="leak",
        )
    assert count(db) == 0


# ── agent tool calls (C1) ───────────────────────────────────────────────────


def test_tool_call_defaults_and_json_roundtrip(db, incident, make_tool_call):
    call = make_tool_call(incident["id"], input_json={"query": "level=error", "window": {"minutes": 30}})
    stored = db.get(AgentToolCall, call.id)
    assert stored.is_write_operation is False
    assert stored.input_json == {"query": "level=error", "window": {"minutes": 30}}
    assert stored.prompt_tokens is None and stored.cost_usd is None


def test_write_operations_are_flagged(db, incident, make_tool_call):
    make_tool_call(incident["id"], is_write=True, tool="apply_change")
    make_tool_call(incident["id"], is_write=False)
    flagged = db.scalars(select(AgentToolCall).where(AgentToolCall.is_write_operation.is_(True))).all()
    assert [c.tool_name for c in flagged] == ["apply_change"]
