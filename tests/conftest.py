"""Shared fixtures: an isolated SQLite database, an API client, and per-role actors in two organizations.

Environment is set BEFORE any `app` import so the app's engine binds to the throwaway test database
and never to whatever a developer's .env points at.
"""

import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="incident-tests-"))
os.environ.update(
    {
        "APP_ENV": "development",
        "APP_DB_URL": f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}",
        "JWT_SECRET": "test-secret-test-secret-test-secret-0123456789",
        "CORS_ALLOWED_ORIGINS": "http://localhost:5173",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.models  # noqa: E402,F401  (register every model on Base.metadata)
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.user import User  # noqa: E402
from app.security.auth import create_access_token  # noqa: E402
from app.security.rbac import Role  # noqa: E402
from app.services import data_source_service  # noqa: E402
from app.worker.celery_app import celery_app  # noqa: E402
from tests.factories import data_source_payload, incident_payload  # noqa: E402


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role
    headers: dict[str, str]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_db():
    """Empties every table after each test so tests never see each other's data."""
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture(autouse=True)
def _reset_connection_probes():
    saved = dict(data_source_service.CONNECTION_PROBES)
    yield
    data_source_service.CONNECTION_PROBES.clear()
    data_source_service.CONNECTION_PROBES.update(saved)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def make_org(db):
    def _make(name: str | None = None) -> uuid.UUID:
        name = name or f"Org {uuid.uuid4().hex[:8]}"
        org = Organization(name=name, slug=name.lower().replace(" ", "-"))
        db.add(org)
        db.commit()
        return org.id

    return _make


@pytest.fixture
def make_actor(db):
    """Creates a user directly in the DB (skipping bcrypt) and returns it with a valid bearer token."""

    def _make(org_id: uuid.UUID, role: Role, *, is_active: bool = True) -> Actor:
        user = User(
            email=f"{role.value}-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="not-a-real-hash",
            role=role,
            org_id=org_id,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        token = create_access_token(str(user.id), claims={"role": role.value, "org_id": str(org_id)})
        return Actor(user.id, org_id, role, {"Authorization": f"Bearer {token}"})

    return _make


@pytest.fixture
def org_a(make_org) -> uuid.UUID:
    return make_org("Org A")


@pytest.fixture
def org_b(make_org) -> uuid.UUID:
    return make_org("Org B")


@pytest.fixture
def admin(make_actor, org_a) -> Actor:
    return make_actor(org_a, Role.ADMIN)


@pytest.fixture
def approver(make_actor, org_a) -> Actor:
    return make_actor(org_a, Role.APPROVER)


@pytest.fixture
def viewer(make_actor, org_a) -> Actor:
    return make_actor(org_a, Role.VIEWER)


@pytest.fixture
def other_admin(make_actor, org_b) -> Actor:
    """An admin of a different organization — used for tenant-isolation checks."""
    return make_actor(org_b, Role.ADMIN)


@pytest.fixture
def task_queue(monkeypatch) -> list[dict]:
    """Replaces the Celery publish with a recorder so tests never need Redis."""
    sent: list[dict] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kwargs: sent.append({"name": name, **kwargs}))
    return sent


@pytest.fixture
def broken_task_queue(monkeypatch) -> None:
    def _fail(*args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(celery_app, "send_task", _fail)


@pytest.fixture
def create_incident(client):
    def _create(actor: Actor, **overrides) -> dict:
        response = client.post("/api/incidents", json=incident_payload(**overrides), headers=actor.headers)
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def create_data_source(client):
    def _create(actor: Actor, **overrides) -> dict:
        response = client.post("/api/data-sources", json=data_source_payload(**overrides), headers=actor.headers)
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def make_approval(db):
    """Inserts an approval row. Rows get strictly increasing created_at so 'newest wins' is deterministic."""
    from datetime import datetime, timedelta, timezone

    from app.models.approval import Approval, ApprovalStatus

    counter = {"n": 0}
    base = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    def _make(incident_id, stage, status, *, decided_by=None, reason=None) -> Approval:
        counter["n"] += 1
        created_at = base + timedelta(minutes=counter["n"])
        decided = status != ApprovalStatus.PENDING
        if status == ApprovalStatus.REJECTED and reason is None:
            reason = "not safe"
        approval = Approval(
            incident_id=uuid.UUID(str(incident_id)),
            stage=stage,
            status=status,
            decided_by=(decided_by or uuid.uuid4()) if decided else None,
            decided_at=created_at if decided else None,
            reason=reason,
            created_at=created_at,
        )
        db.add(approval)
        db.commit()
        return approval

    return _make
