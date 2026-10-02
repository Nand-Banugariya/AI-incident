"""Incident CRUD, tenant isolation and the /investigate trigger."""

import uuid

from sqlalchemy import select

from app.models.incident import Incident, IncidentState
from app.models.investigation import IncidentStateTransition, InvestigationRun, InvestigationRunStatus
from app.worker.celery_app import INVESTIGATION_TASK_NAME, celery_app
from tests.factories import incident_payload


def test_create_incident_defaults_and_normalisation(admin, create_incident):
    incident = create_incident(admin, environment=" Production ", service=" payments ")
    assert incident["state"] == IncidentState.CREATED.value
    assert incident["environment"] == "production"
    assert incident["service"] == "payments"
    assert incident["org_id"] == str(admin.org_id)
    assert incident["created_by"] == str(admin.user_id)
    assert float(incident["total_llm_cost_usd"]) == 0


def test_each_incident_gets_its_own_langgraph_thread(create_incident, admin):
    first, second = create_incident(admin), create_incident(admin)
    assert first["langgraph_thread_id"] != second["langgraph_thread_id"]


def test_create_incident_records_initial_transition(create_incident, admin, db):
    incident = create_incident(admin)
    rows = db.scalars(
        select(IncidentStateTransition).where(IncidentStateTransition.incident_id == uuid.UUID(incident["id"]))
    ).all()
    assert [(r.from_state, r.to_state, r.changed_by) for r in rows] == [(None, IncidentState.CREATED, admin.user_id)]


def test_create_incident_rejects_inverted_time_window(client, admin):
    payload = incident_payload(start_time="2026-10-01T11:00:00Z", end_time="2026-10-01T10:00:00Z")
    assert client.post("/api/incidents", json=payload, headers=admin.headers).status_code == 422


def test_create_incident_requires_fields(client, admin):
    assert client.post("/api/incidents", json={}, headers=admin.headers).status_code == 422


def test_list_is_scoped_to_org_and_paginated(client, admin, other_admin, create_incident):
    for _ in range(3):
        create_incident(admin)
    create_incident(other_admin)

    listed = client.get("/api/incidents", headers=admin.headers).json()
    assert len(listed) == 3
    assert {i["org_id"] for i in listed} == {str(admin.org_id)}

    assert len(client.get("/api/incidents?limit=2&offset=2", headers=admin.headers).json()) == 1
    assert client.get("/api/incidents?limit=0", headers=admin.headers).status_code == 422
    assert client.get("/api/incidents?limit=101", headers=admin.headers).status_code == 422


def test_get_incident_and_cross_org_404(client, admin, other_admin, create_incident):
    incident = create_incident(admin)
    assert client.get(f"/api/incidents/{incident['id']}", headers=admin.headers).status_code == 200
    assert client.get(f"/api/incidents/{incident['id']}", headers=other_admin.headers).status_code == 404
    assert client.get(f"/api/incidents/{uuid.uuid4()}", headers=admin.headers).status_code == 404
    assert client.get("/api/incidents/not-a-uuid", headers=admin.headers).status_code == 422


def test_delete_incident(client, admin, create_incident):
    incident = create_incident(admin)
    assert client.delete(f"/api/incidents/{incident['id']}", headers=admin.headers).status_code == 204
    assert client.get(f"/api/incidents/{incident['id']}", headers=admin.headers).status_code == 404
    assert client.delete(f"/api/incidents/{incident['id']}", headers=admin.headers).status_code == 404


def test_delete_incident_cross_org_404_and_untouched(client, admin, other_admin, create_incident):
    incident = create_incident(admin)
    assert client.delete(f"/api/incidents/{incident['id']}", headers=other_admin.headers).status_code == 404
    assert client.get(f"/api/incidents/{incident['id']}", headers=admin.headers).status_code == 200


def test_delete_blocked_while_investigation_running(client, admin, create_incident, task_queue):
    incident = create_incident(admin)
    assert client.post(f"/api/incidents/{incident['id']}/investigate", headers=admin.headers).status_code == 202
    assert client.delete(f"/api/incidents/{incident['id']}", headers=admin.headers).status_code == 409


# ── /investigate ───────────────────────────────────────────────────────────


def test_investigate_queues_run_and_moves_to_planning(client, admin, create_incident, task_queue, db):
    incident = create_incident(admin)
    response = client.post(f"/api/incidents/{incident['id']}/investigate", headers=admin.headers)

    assert response.status_code == 202
    body = response.json()
    assert body["incident_state"] == IncidentState.PLANNING.value
    assert body["run"]["status"] == InvestigationRunStatus.QUEUED.value

    assert len(task_queue) == 1
    assert task_queue[0]["name"] == INVESTIGATION_TASK_NAME
    assert task_queue[0]["task_id"] == body["run"]["id"]  # run id doubles as the Celery task id
    assert task_queue[0]["args"] == [body["run"]["id"]]

    transitions = db.scalars(
        select(IncidentStateTransition).where(IncidentStateTransition.incident_id == uuid.UUID(incident["id"]))
    ).all()
    assert (IncidentState.CREATED, IncidentState.PLANNING) in {(t.from_state, t.to_state) for t in transitions}


def test_second_investigate_while_active_is_409_and_not_enqueued(client, admin, create_incident, task_queue):
    incident = create_incident(admin)
    url = f"/api/incidents/{incident['id']}/investigate"
    assert client.post(url, headers=admin.headers).status_code == 202
    assert client.post(url, headers=admin.headers).status_code == 409
    assert len(task_queue) == 1


def test_investigate_queue_failure_returns_503_and_restores_state(
    client, admin, create_incident, broken_task_queue, db
):
    incident = create_incident(admin)
    response = client.post(f"/api/incidents/{incident['id']}/investigate", headers=admin.headers)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    db.expire_all()
    assert db.get(Incident, uuid.UUID(incident["id"])).state == IncidentState.CREATED
    assert db.scalars(select(InvestigationRun)).one().status == InvestigationRunStatus.FAILED


def test_investigate_after_failed_enqueue_can_be_retried(client, admin, create_incident, monkeypatch):
    incident = create_incident(admin)
    url = f"/api/incidents/{incident['id']}/investigate"

    def _down(*args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(celery_app, "send_task", _down)
    assert client.post(url, headers=admin.headers).status_code == 503

    monkeypatch.setattr(celery_app, "send_task", lambda *args, **kwargs: None)
    assert client.post(url, headers=admin.headers).status_code == 202


def test_investigate_cross_org_404(client, admin, other_admin, create_incident, task_queue):
    incident = create_incident(admin)
    assert client.post(f"/api/incidents/{incident['id']}/investigate", headers=other_admin.headers).status_code == 404
    assert task_queue == []


def test_investigate_rejected_in_non_startable_state(client, admin, create_incident, task_queue, db):
    incident = create_incident(admin)
    db.get(Incident, uuid.UUID(incident["id"])).state = IncidentState.WAITING_PLAN_APPROVAL
    db.commit()
    assert client.post(f"/api/incidents/{incident['id']}/investigate", headers=admin.headers).status_code == 409
    assert task_queue == []
