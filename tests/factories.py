"""Request payload builders shared by tests."""


def incident_payload(**overrides) -> dict:
    payload = {
        "environment": "Production",
        "service": "payments",
        "start_time": "2026-10-01T10:00:00Z",
        "end_time": "2026-10-01T10:30:00Z",
        "error_hint": "500s on /checkout",
    }
    payload.update(overrides)
    return payload


def data_source_payload(**overrides) -> dict:
    payload = {
        "name": "prod-db",
        "provider_type": "postgresql",
        "environment": "production",
        "configuration_json": {"host": "db.internal", "log_paths": ["/var/log/pg.log"]},
        "credential_reference": "vault:secret/pg",
        "capabilities": ["database_logs", "queries"],
    }
    payload.update(overrides)
    return payload
