"""GET /incidents/{id}/evidence (C2) and its interaction with data source deletion."""

import uuid

import pytest

from app.models.evidence import EvidenceType
from tests.integration.conftest import at


def url(incident_id) -> str:
    return f"/api/incidents/{incident_id}/evidence"


def get(client, incident_id, actor, **params):
    response = client.get(url(incident_id), params=params, headers=actor.headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_for_a_new_incident(client, admin, incident):
    assert get(client, incident["id"], admin) == []


def test_returns_fields_but_not_the_internal_hash(client, admin, incident, source, make_evidence):
    make_evidence(
        incident["id"], source["id"], content="raw", normalized_content="norm", service="payments",
        correlation_id="req-1", severity="error", timestamp=at(3),
    )
    [item] = get(client, incident["id"], admin)
    assert item["content"] == "raw" and item["normalized_content"] == "norm"
    assert (item["service"], item["correlation_id"], item["severity"]) == ("payments", "req-1", "error")
    assert item["evidence_type"] == "application_log"
    assert item["source_id"] == source["id"] and item["incident_id"] == incident["id"]
    assert "content_hash" not in item


def test_ordered_by_time_whatever_the_insert_order(client, admin, incident, source, make_evidence):
    for minute in (9, 1, 5):
        make_evidence(incident["id"], source["id"], content=f"at {minute}", timestamp=at(minute))
    assert [e["content"] for e in get(client, incident["id"], admin)] == ["at 1", "at 5", "at 9"]


def test_only_this_incidents_evidence(client, admin, create_incident, incident, source, make_evidence):
    other = create_incident(admin)
    make_evidence(incident["id"], source["id"], content="mine")
    make_evidence(other["id"], source["id"], content="theirs")
    assert [e["content"] for e in get(client, incident["id"], admin)] == ["mine"]


# ── filters ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mixed(incident, source, admin, create_data_source, make_evidence):
    second = create_data_source(admin, name="db-logs", provider_type="postgresql")
    make_evidence(incident["id"], source["id"], content="app err", timestamp=at(1), service="payments",
                  severity="error", correlation_id="req-1")
    make_evidence(incident["id"], source["id"], content="app warn", timestamp=at(2), service="checkout",
                  severity="warning", correlation_id="req-2")
    make_evidence(incident["id"], second["id"], content="slow query", timestamp=at(3), service="payments",
                  severity="warning", correlation_id="req-1", evidence_type=EvidenceType.DATABASE_LOG)
    make_evidence(incident["id"], second["id"], content="metric spike", timestamp=at(10),
                  evidence_type=EvidenceType.METRIC)
    return {"incident": incident["id"], "app": source["id"], "db": second["id"]}


def contents(client, ids, actor, **params):
    return [e["content"] for e in get(client, ids["incident"], actor, **params)]


def test_filter_by_type(client, admin, mixed):
    assert contents(client, mixed, admin, evidence_type="database_log") == ["slow query"]
    assert contents(client, mixed, admin, evidence_type="metric") == ["metric spike"]


def test_filter_by_source(client, admin, mixed):
    assert contents(client, mixed, admin, source_id=mixed["db"]) == ["slow query", "metric spike"]


def test_filter_by_service_correlation_and_severity(client, admin, mixed):
    assert contents(client, mixed, admin, service="payments") == ["app err", "slow query"]
    assert contents(client, mixed, admin, correlation_id="req-1") == ["app err", "slow query"]
    assert contents(client, mixed, admin, severity="WARNING") == ["app warn", "slow query"]  # case-insensitive


def test_filter_by_time_range_is_inclusive(client, admin, mixed):
    assert contents(client, mixed, admin, start=at(2).isoformat(), end=at(3).isoformat()) == ["app warn", "slow query"]
    assert contents(client, mixed, admin, start=at(4).isoformat()) == ["metric spike"]
    assert contents(client, mixed, admin, end=at(0).isoformat()) == []


def test_filters_combine(client, admin, mixed):
    assert contents(client, mixed, admin, service="payments", severity="warning") == ["slow query"]
    assert contents(client, mixed, admin, service="payments", evidence_type="metric") == []


def test_pagination(client, admin, mixed):
    assert contents(client, mixed, admin, limit=2) == ["app err", "app warn"]
    assert contents(client, mixed, admin, limit=2, offset=2) == ["slow query", "metric spike"]
    assert contents(client, mixed, admin, offset=10) == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"evidence_type": "bogus"},
                                    {"source_id": "nope"}, {"start": "yesterday-ish"}])
def test_bad_query_params_are_422(client, admin, incident, params):
    assert client.get(url(incident["id"]), params=params, headers=admin.headers).status_code == 422


# ── access ──────────────────────────────────────────────────────────────────


def test_viewers_can_read_evidence(client, viewer, incident, source, make_evidence):
    make_evidence(incident["id"], source["id"])
    assert len(get(client, incident["id"], viewer)) == 1


def test_requires_authentication(client, incident):
    assert client.get(url(incident["id"])).status_code == 401


def test_other_orgs_get_404_not_the_evidence(client, other_admin, incident, source, make_evidence):
    make_evidence(incident["id"], source["id"])
    assert client.get(url(incident["id"]), headers=other_admin.headers).status_code == 404


def test_unknown_and_malformed_incident(client, admin):
    assert client.get(url(uuid.uuid4()), headers=admin.headers).status_code == 404
    assert client.get(url("not-a-uuid"), headers=admin.headers).status_code == 422


# ── a source with evidence is part of the audit trail ───────────────────────


def test_cannot_delete_a_data_source_that_has_evidence(client, admin, incident, source, make_evidence):
    make_evidence(incident["id"], source["id"])
    response = client.delete(f"/api/data-sources/{source['id']}", headers=admin.headers)
    assert response.status_code == 409
    assert "disable it instead" in response.json()["detail"]
    assert client.get(f"/api/data-sources/{source['id']}", headers=admin.headers).status_code == 200


def test_such_a_source_can_still_be_disabled(client, admin, incident, source, make_evidence):
    make_evidence(incident["id"], source["id"])
    response = client.put(f"/api/data-sources/{source['id']}", json={"is_enabled": False}, headers=admin.headers)
    assert response.status_code == 200 and response.json()["is_enabled"] is False


def test_a_source_without_evidence_is_still_deletable(client, admin, create_data_source, source, incident, make_evidence):
    unused = create_data_source(admin, name="unused", provider_type="loki")
    make_evidence(incident["id"], source["id"])
    assert client.delete(f"/api/data-sources/{unused['id']}", headers=admin.headers).status_code == 204
