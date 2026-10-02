"""Data source registration, validation, tenant isolation, capabilities and connection tests."""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.data_source import DataSource, DataSourceCapability, DataSourceHealth, DataSourceHealthStatus
from app.services import data_source_service
from tests.factories import data_source_payload

BASE = "/api/data-sources"


def test_create_normalises_input_and_hides_credential_reference(admin, create_data_source):
    ds = create_data_source(
        admin,
        name="  prod-db  ",
        provider_type="PostgreSQL",
        environment="Production",
        capabilities=["database_logs", "Database_Logs", "queries"],
    )
    assert (ds["name"], ds["provider_type"], ds["environment"]) == ("prod-db", "postgresql", "production")
    assert ds["org_id"] == str(admin.org_id)
    assert ds["has_credential"] is True
    assert "credential_reference" not in ds
    assert sorted(c["capability"] for c in ds["capabilities"]) == ["database_logs", "queries"]


def test_credential_reference_is_stored_but_never_returned(client, admin, create_data_source, db):
    ds = create_data_source(admin)
    assert "vault:secret/pg" not in client.get(f"{BASE}/{ds['id']}", headers=admin.headers).text
    assert db.get(DataSource, uuid.UUID(ds["id"])).credential_reference == "vault:secret/pg"


def test_create_without_credential(admin, create_data_source):
    assert create_data_source(admin, credential_reference=None)["has_credential"] is False


@pytest.mark.parametrize(
    "config",
    [
        {"password": "hunter2"},
        {"host": "x", "auth": {"api_key": "abc"}},
        {"hosts": [{"name": "a", "Token": "t"}]},
        {"client_secret": "s"},
        {"authorization": "Bearer abc"},
    ],
)
def test_credentials_in_configuration_are_rejected(client, admin, config):
    response = client.post(BASE, json=data_source_payload(configuration_json=config), headers=admin.headers)
    assert response.status_code == 422
    assert "credentials" in response.text


def test_rejection_names_the_nested_key(client, admin):
    config = {"host": "x", "auth": {"api_key": "abc"}}
    response = client.post(BASE, json=data_source_payload(configuration_json=config), headers=admin.headers)
    assert "auth.api_key" in response.text


@pytest.mark.parametrize(
    "override",
    [
        {"capabilities": ["bad cap!"]},
        {"provider_type": "Not Valid"},
        {"environment": "pr od"},
        {"name": "   "},
        {"configuration_json": "not-an-object"},
    ],
)
def test_create_validation(client, admin, override):
    assert client.post(BASE, json=data_source_payload(**override), headers=admin.headers).status_code == 422


def test_list_get_and_filters(client, admin, viewer, create_data_source):
    create_data_source(admin, name="pg-prod", provider_type="postgresql", environment="production")
    create_data_source(admin, name="k8s-stg", provider_type="kubernetes", environment="staging")

    assert len(client.get(BASE, headers=viewer.headers).json()) == 2
    assert [d["name"] for d in client.get(f"{BASE}?environment=STAGING", headers=viewer.headers).json()] == ["k8s-stg"]
    assert [d["name"] for d in client.get(f"{BASE}?provider_type=postgresql", headers=viewer.headers).json()] == ["pg-prod"]
    assert client.get(f"{BASE}?environment=staging&provider_type=postgresql", headers=viewer.headers).json() == []
    assert len(client.get(f"{BASE}?limit=1", headers=viewer.headers).json()) == 1
    assert client.get(f"{BASE}?limit=0", headers=viewer.headers).status_code == 422


def test_viewer_can_read_one_and_its_capabilities(client, admin, viewer, create_data_source):
    ds = create_data_source(admin)
    assert client.get(f"{BASE}/{ds['id']}", headers=viewer.headers).status_code == 200
    caps = client.get(f"{BASE}/{ds['id']}/capabilities", headers=viewer.headers).json()
    assert caps == [
        {"capability": "database_logs", "is_enabled": True},
        {"capability": "queries", "is_enabled": True},
    ]


def test_unknown_and_malformed_ids(client, admin):
    assert client.get(f"{BASE}/{uuid.uuid4()}", headers=admin.headers).status_code == 404
    assert client.get(f"{BASE}/not-a-uuid", headers=admin.headers).status_code == 422


# ── tenant isolation ───────────────────────────────────────────────────────


def test_other_org_cannot_see_or_touch_a_data_source(client, admin, other_admin, create_data_source, db):
    ds = create_data_source(admin)
    assert client.get(BASE, headers=other_admin.headers).json() == []

    for method, path, kwargs in [
        ("GET", f"{BASE}/{ds['id']}", {}),
        ("GET", f"{BASE}/{ds['id']}/capabilities", {}),
        ("PUT", f"{BASE}/{ds['id']}", {"json": {"name": "hijacked"}}),
        ("DELETE", f"{BASE}/{ds['id']}", {}),
        ("POST", f"{BASE}/{ds['id']}/test", {}),
    ]:
        response = client.request(method, path, headers=other_admin.headers, **kwargs)
        assert response.status_code == 404, f"{method} {path}"

    row = db.get(DataSource, uuid.UUID(ds["id"]))
    assert row.name == "prod-db"  # untouched


def test_created_source_belongs_to_callers_org_not_a_payload_org(client, admin, org_b):
    response = client.post(BASE, json={**data_source_payload(), "org_id": str(org_b)}, headers=admin.headers)
    assert response.status_code == 201
    assert response.json()["org_id"] == str(admin.org_id)


# ── update ─────────────────────────────────────────────────────────────────


def test_update_changes_only_supplied_fields(client, admin, create_data_source):
    ds = create_data_source(admin)
    response = client.put(f"{BASE}/{ds['id']}", json={"name": "renamed"}, headers=admin.headers)
    body = response.json()
    assert response.status_code == 200
    assert body["name"] == "renamed"
    assert body["configuration_json"] == ds["configuration_json"]
    assert body["has_credential"] is True
    assert body["is_enabled"] is True


def test_update_replaces_capability_set(client, admin, create_data_source, db):
    ds = create_data_source(admin, capabilities=["a_cap", "b_cap"])
    body = client.put(f"{BASE}/{ds['id']}", json={"capabilities": ["b_cap", "c_cap"]}, headers=admin.headers).json()
    assert sorted(c["capability"] for c in body["capabilities"]) == ["b_cap", "c_cap"]
    assert db.scalar(select(func.count()).select_from(DataSourceCapability)) == 2  # a_cap row really deleted


def test_update_can_clear_credential_and_disable(client, admin, create_data_source):
    ds = create_data_source(admin)
    body = client.put(
        f"{BASE}/{ds['id']}", json={"credential_reference": None, "is_enabled": False}, headers=admin.headers
    ).json()
    assert body["has_credential"] is False
    assert body["is_enabled"] is False


def test_update_null_on_required_field_is_ignored(client, admin, create_data_source):
    ds = create_data_source(admin)
    assert client.put(f"{BASE}/{ds['id']}", json={"name": None}, headers=admin.headers).json()["name"] == "prod-db"


def test_provider_type_is_immutable(client, admin, create_data_source):
    ds = create_data_source(admin)
    body = client.put(f"{BASE}/{ds['id']}", json={"provider_type": "kubernetes"}, headers=admin.headers).json()
    assert body["provider_type"] == "postgresql"


def test_update_rejects_credentials_in_configuration(client, admin, create_data_source):
    ds = create_data_source(admin)
    response = client.put(
        f"{BASE}/{ds['id']}", json={"configuration_json": {"password": "x"}}, headers=admin.headers
    )
    assert response.status_code == 422


# ── delete ─────────────────────────────────────────────────────────────────


def test_delete_cascades_to_capabilities_and_health(client, admin, create_data_source, db):
    ds = create_data_source(admin)
    data_source_service.CONNECTION_PROBES["postgresql"] = lambda source: None
    assert client.post(f"{BASE}/{ds['id']}/test", headers=admin.headers).status_code == 200

    assert client.delete(f"{BASE}/{ds['id']}", headers=admin.headers).status_code == 204
    assert client.get(f"{BASE}/{ds['id']}", headers=admin.headers).status_code == 404
    for model in (DataSource, DataSourceCapability, DataSourceHealth):
        assert db.scalar(select(func.count()).select_from(model)) == 0


# ── connection test ────────────────────────────────────────────────────────


def test_connection_test_without_probe_is_501(client, admin, create_data_source, db):
    ds = create_data_source(admin)
    assert client.post(f"{BASE}/{ds['id']}/test", headers=admin.headers).status_code == 501
    assert db.scalar(select(func.count()).select_from(DataSourceHealth)) == 0


def test_connection_test_healthy_records_health(client, admin, create_data_source, db):
    ds = create_data_source(admin)
    data_source_service.CONNECTION_PROBES["postgresql"] = lambda source: None

    response = client.post(f"{BASE}/{ds['id']}/test", headers=admin.headers)
    assert response.status_code == 200
    assert response.json()["status"] == DataSourceHealthStatus.HEALTHY.value
    assert response.json()["error_message"] is None

    health = db.scalars(select(DataSourceHealth)).one()
    assert str(health.data_source_id) == ds["id"]
    assert health.status == DataSourceHealthStatus.HEALTHY
    assert health.response_time_ms is not None


def test_connection_test_failure_is_a_result_not_a_server_error(client, admin, create_data_source, db):
    ds = create_data_source(admin)

    def _refuse(source):
        raise ConnectionError("refused")

    data_source_service.CONNECTION_PROBES["postgresql"] = _refuse
    response = client.post(f"{BASE}/{ds['id']}/test", headers=admin.headers)

    assert response.status_code == 200
    assert response.json()["status"] == DataSourceHealthStatus.UNREACHABLE.value
    assert response.json()["error_message"] == "ConnectionError: refused"
    assert db.scalars(select(DataSourceHealth)).one().status == DataSourceHealthStatus.UNREACHABLE


def test_connection_test_error_message_is_truncated(client, admin, create_data_source):
    ds = create_data_source(admin)

    def _verbose(source):
        raise RuntimeError("x" * 5000)

    data_source_service.CONNECTION_PROBES["postgresql"] = _verbose
    message = client.post(f"{BASE}/{ds['id']}/test", headers=admin.headers).json()["error_message"]
    assert len(message) <= 500
