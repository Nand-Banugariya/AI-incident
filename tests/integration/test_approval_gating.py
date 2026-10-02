"""The most important test file in the project: no write-capable endpoint opens without an approval row.

For every write endpoint, try every combination of present approvals and assert 403 until the exact
right approval exists. The expectations below are written out by hand (the spec's table), NOT derived
from the application code, so a wrong mapping in the code cannot hide itself.

Gated actions are stubs for now: once both gates pass they answer 501, which is how these tests tell
"gate open" (501) from "gate closed" (403).
"""

import itertools
import uuid

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import func, select

from app.api.deps import require_approval, require_permission
from app.models.approval import Approval, ApprovalStage, ApprovalStatus
from app.models.incident import Incident, IncidentState, RiskLevel

PLAN, CODE, PRODUCTION = ApprovalStage.PLAN, ApprovalStage.CODE, ApprovalStage.PRODUCTION
APPROVED, PENDING, REJECTED = ApprovalStatus.APPROVED, ApprovalStatus.PENDING, ApprovalStatus.REJECTED

# endpoint -> the single approval stage that unlocks it
WRITE_ENDPOINTS = {
    "implement": PLAN,
    "create-pr": CODE,
    "deploy-qa": CODE,
    "deploy-production": PRODUCTION,
}
GATE_OPEN, GATE_CLOSED = 501, 403

ALL_STAGES = list(ApprovalStage)
STAGE_COMBINATIONS = [
    frozenset(combo) for size in range(4) for combo in itertools.combinations(ALL_STAGES, size)
]  # 0, 1, 2 and 3 approvals present: 8 combinations


def label(combo: frozenset) -> str:
    return "+".join(sorted(s.value for s in combo)) or "none"


@pytest.fixture
def incident_id(admin, create_incident) -> str:
    return create_incident(admin)["id"]


def post(client, incident_id, endpoint, actor=None):
    return client.post(f"/api/incidents/{incident_id}/{endpoint}", headers=actor.headers if actor else None)


# ── the matrix: every endpoint x every combination of approvals ─────────────


@pytest.mark.parametrize("combo", STAGE_COMBINATIONS, ids=label)
@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_gate_opens_only_with_the_exact_stage_approved(client, approver, incident_id, make_approval, endpoint, combo):
    for stage in combo:
        make_approval(incident_id, stage, APPROVED)

    expected = GATE_OPEN if WRITE_ENDPOINTS[endpoint] in combo else GATE_CLOSED
    assert post(client, incident_id, endpoint, approver).status_code == expected


@pytest.mark.parametrize("not_approved", [PENDING, REJECTED])
@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_pending_or_rejected_stage_never_opens_even_if_other_stages_are_approved(
    client, approver, incident_id, make_approval, endpoint, not_approved
):
    needed = WRITE_ENDPOINTS[endpoint]
    for stage in ALL_STAGES:
        make_approval(incident_id, stage, not_approved if stage == needed else APPROVED)
    assert post(client, incident_id, endpoint, approver).status_code == GATE_CLOSED


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_revoked_approval_closes_the_gate_again(client, approver, incident_id, make_approval, endpoint):
    needed = WRITE_ENDPOINTS[endpoint]
    make_approval(incident_id, needed, APPROVED)
    assert post(client, incident_id, endpoint, approver).status_code == GATE_OPEN
    make_approval(incident_id, needed, REJECTED)
    assert post(client, incident_id, endpoint, approver).status_code == GATE_CLOSED


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_approving_another_incident_does_not_help(client, admin, approver, create_incident, make_approval, endpoint):
    mine, other = create_incident(admin)["id"], create_incident(admin)["id"]
    for stage in ALL_STAGES:
        make_approval(other, stage, APPROVED)
    assert post(client, mine, endpoint, approver).status_code == GATE_CLOSED
    assert post(client, other, endpoint, approver).status_code == GATE_OPEN


# ── the two layers are independent: each one alone is not enough ────────────


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_layer_1_role_is_required_even_when_approved(client, viewer, incident_id, make_approval, endpoint):
    for stage in ALL_STAGES:
        make_approval(incident_id, stage, APPROVED)
    response = post(client, incident_id, endpoint, viewer)
    assert response.status_code == 403
    assert "Missing required permission" in response.json()["detail"]


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_layer_2_approval_is_required_even_for_admins(client, admin, incident_id, endpoint):
    response = post(client, incident_id, endpoint, admin)
    assert response.status_code == 403
    assert "No approved" in response.json()["detail"]


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_unauthenticated_is_401(client, incident_id, make_approval, endpoint):
    for stage in ALL_STAGES:
        make_approval(incident_id, stage, APPROVED)
    assert post(client, incident_id, endpoint).status_code == 401


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_other_orgs_cannot_probe_or_use_an_incident(client, other_admin, incident_id, make_approval, endpoint):
    for stage in ALL_STAGES:
        make_approval(incident_id, stage, APPROVED)
    assert post(client, incident_id, endpoint, other_admin).status_code == 404


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_unknown_incident_is_404_and_malformed_is_422(client, admin, endpoint):
    assert post(client, uuid.uuid4(), endpoint, admin).status_code == 404
    assert post(client, "not-a-uuid", endpoint, admin).status_code == 422


# ── a closed or stubbed gate has no side effects ────────────────────────────


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_requests_change_nothing(client, db, approver, incident_id, make_approval, endpoint):
    make_approval(incident_id, WRITE_ENDPOINTS[endpoint], APPROVED)
    before = (db.scalar(select(func.count()).select_from(Approval)), db.get(Incident, uuid.UUID(incident_id)).state)

    post(client, incident_id, endpoint, approver)  # open gate -> 501 stub
    for stage in ALL_STAGES:
        post(client, incident_id, endpoint, approver)

    db.expire_all()
    after = (db.scalar(select(func.count()).select_from(Approval)), db.get(Incident, uuid.UUID(incident_id)).state)
    assert after == before


# ── end to end: approvals made through the real endpoints drive the gates ───


def test_full_lifecycle_through_the_real_approval_endpoints(client, db, admin, approver, create_incident):
    incident_id = create_incident(admin)["id"]
    incident = db.get(Incident, uuid.UUID(incident_id))
    incident.risk_level = RiskLevel.LOW

    def at_gate(state: IncidentState) -> None:
        db.expire_all()
        db.get(Incident, uuid.UUID(incident_id)).state = state
        db.commit()

    def decide(action: str, actor=approver, **json) -> int:
        return client.post(f"/api/incidents/{incident_id}/{action}", json=json or None, headers=actor.headers).status_code

    def gates() -> dict[str, int]:
        return {e: post(client, incident_id, e, approver).status_code for e in WRITE_ENDPOINTS}

    closed = {e: GATE_CLOSED for e in WRITE_ENDPOINTS}
    assert gates() == closed

    at_gate(IncidentState.WAITING_PLAN_APPROVAL)
    assert decide("approve-plan") == 200
    assert gates() == {**closed, "implement": GATE_OPEN}

    at_gate(IncidentState.WAITING_CODE_APPROVAL)
    assert decide("approve-code") == 200
    assert gates() == {**closed, "implement": GATE_OPEN, "create-pr": GATE_OPEN, "deploy-qa": GATE_OPEN}

    at_gate(IncidentState.WAITING_PRODUCTION_APPROVAL)
    assert decide("approve-production") == 200
    assert gates() == {e: GATE_OPEN for e in WRITE_ENDPOINTS}

    at_gate(IncidentState.WAITING_CODE_APPROVAL)
    assert decide("reject-code", reason="regression found in QA") == 200
    assert gates() == {**{e: GATE_OPEN for e in WRITE_ENDPOINTS}, "create-pr": GATE_CLOSED, "deploy-qa": GATE_CLOSED}


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_blocked_self_approval_leaves_the_gate_closed(client, db, approver, create_incident, endpoint):
    """Segregation of duties end to end: the creator's refused approval must not open anything."""
    incident_id = create_incident(approver)["id"]
    incident = db.get(Incident, uuid.UUID(incident_id))
    incident.state, incident.risk_level = IncidentState.WAITING_PLAN_APPROVAL, RiskLevel.HIGH
    db.commit()

    approve = client.post(f"/api/incidents/{incident_id}/approve-plan", headers=approver.headers)
    assert approve.status_code == 403
    assert post(client, incident_id, endpoint, approver).status_code == GATE_CLOSED


# ── structural guard: deny by default ───────────────────────────────────────

# POST /incidents/{id}/<action> routes that legitimately do NOT sit behind require_approval.
# Adding to this set is a deliberate, reviewable decision: anything not listed here must be gated.
UNGATED_INCIDENT_POSTS = {"investigate"} | {f"{verb}-{s.value}" for verb in ("approve", "reject") for s in ApprovalStage}


def _api_routes(routes):
    """Flattens (possibly nested) routers into APIRoute objects."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        inner = getattr(route, "original_router", route)
        nested = getattr(inner, "routes", None)
        if nested is not None and inner is not route:
            yield from _api_routes(nested)


def _dependency_calls(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_calls(sub)


@pytest.fixture(scope="module")
def incident_action_routes():
    from app.main import app

    routes = list(_api_routes(app.routes))
    assert len(routes) >= 25, "route discovery found too few routes; the introspection helper is broken"
    prefix = "/incidents/{incident_id}/"
    return {r.path[len(prefix):]: r for r in routes if r.path.startswith(prefix) and "POST" in r.methods}


def test_every_write_endpoint_exists(incident_action_routes):
    assert set(WRITE_ENDPOINTS) <= set(incident_action_routes)


@pytest.mark.parametrize("endpoint,stage", WRITE_ENDPOINTS.items())
def test_each_write_endpoint_depends_on_both_layers_with_the_right_stage(incident_action_routes, endpoint, stage):
    calls = list(_dependency_calls(incident_action_routes[endpoint].dependant))
    assert [c.required_stage for c in calls if hasattr(c, "required_stage")] == [stage]
    assert [c.required_permission for c in calls if hasattr(c, "required_permission")] == ["approvals:write"]


def test_no_incident_post_route_is_ungated_unless_explicitly_allowed(incident_action_routes):
    """Deny by default: a new POST /incidents/{id}/<action> must use require_approval or be added to the allowlist."""
    ungated = {
        name
        for name, route in incident_action_routes.items()
        if not any(hasattr(c, "required_stage") for c in _dependency_calls(route.dependant))
    }
    assert ungated == UNGATED_INCIDENT_POSTS, (
        f"Ungated write route(s) found: {sorted(ungated - UNGATED_INCIDENT_POSTS)}; "
        f"missing allowlisted route(s): {sorted(UNGATED_INCIDENT_POSTS - ungated)}"
    )


def test_the_dependency_factories_are_the_real_ones(incident_action_routes):
    """Guards the guard: markers must come from the genuine factories, not from lookalike functions."""
    calls = list(_dependency_calls(incident_action_routes["implement"].dependant))
    stage_checker = next(c for c in calls if hasattr(c, "required_stage"))
    permission_checker = next(c for c in calls if hasattr(c, "required_permission"))
    assert stage_checker.__qualname__ == require_approval(PLAN).__qualname__
    assert permission_checker.__qualname__ == require_permission("x").__qualname__
