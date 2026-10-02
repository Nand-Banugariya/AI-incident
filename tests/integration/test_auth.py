"""Registration, login and bearer-token validation."""

import uuid
from datetime import timedelta

import jwt
import pytest

from app.models.user import User
from app.security.auth import create_access_token
from app.security.rbac import Role

REGISTER = {"email": "Boss@Example.com", "password": "correct-horse", "organization_name": "Acme Corp"}


def test_register_creates_org_and_admin(client):
    response = client.post("/api/auth/register", json=REGISTER)
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == Role.ADMIN.value
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["org_id"]


def test_register_token_is_usable(client):
    token = client.post("/api/auth/register", json=REGISTER).json()["access_token"]
    response = client.get("/api/incidents", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == []


def test_register_duplicate_email_is_case_insensitive(client):
    client.post("/api/auth/register", json=REGISTER)
    again = {**REGISTER, "email": "boss@example.com", "organization_name": "Other Corp"}
    assert client.post("/api/auth/register", json=again).status_code == 409


def test_register_duplicate_org_name_is_case_insensitive(client):
    client.post("/api/auth/register", json=REGISTER)
    again = {**REGISTER, "email": "other@example.com", "organization_name": "ACME CORP"}
    assert client.post("/api/auth/register", json=again).status_code == 409


@pytest.mark.parametrize(
    "override",
    [
        {"password": "short"},
        {"password": "x" * 73},
        {"password": "é" * 40},  # 80 bytes: over the 72-byte bcrypt limit
        {"email": "not-an-email"},
        {"organization_name": "A"},
    ],
)
def test_register_validation(client, override):
    assert client.post("/api/auth/register", json={**REGISTER, **override}).status_code == 422


def test_login_success_is_case_insensitive_on_email(client):
    client.post("/api/auth/register", json=REGISTER)
    response = client.post("/api/auth/login", json={"email": "BOSS@example.COM", "password": REGISTER["password"]})
    assert response.status_code == 200
    assert response.json()["role"] == Role.ADMIN.value


def test_login_wrong_password_and_unknown_email_look_identical(client):
    client.post("/api/auth/register", json=REGISTER)
    wrong_password = client.post("/api/auth/login", json={"email": REGISTER["email"], "password": "wrong-password"})
    unknown_email = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]


def test_login_inactive_user_forbidden(client, db):
    client.post("/api/auth/register", json=REGISTER)
    user = db.query(User).one()
    user.is_active = False
    db.commit()
    response = client.post("/api/auth/login", json={"email": REGISTER["email"], "password": REGISTER["password"]})
    assert response.status_code == 403


def test_protected_route_requires_token(client):
    assert client.get("/api/incidents").status_code == 401


def test_garbage_token_rejected(client):
    assert client.get("/api/incidents", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_expired_token_rejected(client, admin):
    token = create_access_token(str(admin.user_id), expires_delta=timedelta(seconds=-5))
    assert client.get("/api/incidents", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_signed_with_other_secret_rejected(client, admin):
    forged = jwt.encode({"sub": str(admin.user_id)}, "x" * 40, algorithm="HS256")
    assert client.get("/api/incidents", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_token_for_deactivated_user_rejected(client, make_actor, org_a, db):
    actor = make_actor(org_a, Role.ADMIN)
    assert client.get("/api/incidents", headers=actor.headers).status_code == 200
    db.get(User, actor.user_id).is_active = False
    db.commit()
    assert client.get("/api/incidents", headers=actor.headers).status_code == 401


def test_token_for_unknown_user_rejected(client):
    token = create_access_token(str(uuid.uuid4()))
    assert client.get("/api/incidents", headers={"Authorization": f"Bearer {token}"}).status_code == 401
