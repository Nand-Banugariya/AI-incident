"""RBAC matrix: which role may call which endpoint.

Permission checks run before any resource lookup, so denied cases can use random IDs, while allowed
cases are exercised against real resources.
"""

import uuid

import pytest

from app.security.rbac import PERMISSIONS, Role
from tests.factories import data_source_payload, incident_payload

RANDOM_ID = uuid.uuid4()

# (method, path, json body or None, roles allowed past the permission check)
ENDPOINTS = [
    ("GET", "/api/incidents", None, {Role.VIEWER, Role.APPROVER, Role.ADMIN}),
    ("POST", "/api/incidents", incident_payload(), {Role.APPROVER, Role.ADMIN}),
    ("POST", f"/api/incidents/{RANDOM_ID}/investigate", None, {Role.APPROVER, Role.ADMIN}),
    ("DELETE", f"/api/incidents/{RANDOM_ID}", None, {Role.ADMIN}),
    ("GET", "/api/data-sources", None, {Role.VIEWER, Role.APPROVER, Role.ADMIN}),
    ("GET", f"/api/data-sources/{RANDOM_ID}", None, {Role.VIEWER, Role.APPROVER, Role.ADMIN}),
    ("GET", f"/api/data-sources/{RANDOM_ID}/capabilities", None, {Role.VIEWER, Role.APPROVER, Role.ADMIN}),
    ("POST", "/api/data-sources", data_source_payload(), {Role.ADMIN}),
    ("PUT", f"/api/data-sources/{RANDOM_ID}", {"name": "x"}, {Role.ADMIN}),
    ("DELETE", f"/api/data-sources/{RANDOM_ID}", None, {Role.ADMIN}),
    ("POST", f"/api/data-sources/{RANDOM_ID}/test", None, {Role.ADMIN}),
    ("GET", f"/api/incidents/{RANDOM_ID}/approvals", None, {Role.VIEWER, Role.APPROVER, Role.ADMIN}),
    *[
        ("GET", f"/api/incidents/{RANDOM_ID}/{view}", None, {Role.VIEWER, Role.APPROVER, Role.ADMIN})
        for view in ("status", "timeline", "evidence")
    ],
    *[
        ("POST", f"/api/incidents/{RANDOM_ID}/{action}", None, {Role.APPROVER, Role.ADMIN})
        for action in ("implement", "create-pr", "deploy-qa", "deploy-production")
    ],
    *[
        ("POST", f"/api/incidents/{RANDOM_ID}/{action}-{stage}", body, {Role.APPROVER, Role.ADMIN})
        for stage in ("plan", "code", "production")
        for action, body in (("approve", None), ("reject", {"reason": "not safe"}))
    ],
]


@pytest.mark.parametrize("method,path,body,allowed", ENDPOINTS, ids=[f"{m} {p.replace(str(RANDOM_ID), '{id}')}" for m, p, _, _ in ENDPOINTS])
@pytest.mark.parametrize("role", list(Role))
def test_role_matrix(client, make_actor, org_a, task_queue, method, path, body, allowed, role):
    actor = make_actor(org_a, role)
    response = client.request(method, path, json=body, headers=actor.headers)
    if role in allowed:
        # Past the permission check: anything except 401/403 (a random id then yields 404, etc.)
        assert response.status_code not in (401, 403), response.text
    else:
        assert response.status_code == 403, response.text


@pytest.mark.parametrize("method,path,body,_allowed", ENDPOINTS, ids=[f"{m} {p.replace(str(RANDOM_ID), '{id}')}" for m, p, _, _ in ENDPOINTS])
def test_every_endpoint_requires_authentication(client, method, path, body, _allowed):
    assert client.request(method, path, json=body).status_code == 401


# ── The permission table itself ────────────────────────────────────────────


def test_roles_are_strictly_nested():
    assert PERMISSIONS[Role.VIEWER] < PERMISSIONS[Role.APPROVER] < PERMISSIONS[Role.ADMIN]


def test_viewer_is_read_only():
    assert all(p.endswith(":read") for p in PERMISSIONS[Role.VIEWER])


def test_only_admin_manages_data_sources_and_users():
    for permission in ("data_sources:write", "users:write"):
        holders = {role for role, perms in PERMISSIONS.items() if permission in perms}
        assert holders == {Role.ADMIN}


def test_approvals_require_approver_or_admin():
    holders = {role for role, perms in PERMISSIONS.items() if "approvals:write" in perms}
    assert holders == {Role.APPROVER, Role.ADMIN}
