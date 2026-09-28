"""Role-Based Access Control (RBAC) definition and permission mappings."""

from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"  # can see incidents/evidence, cannot approve or trigger anything
    APPROVER = "approver"  # can approve/reject plan, code, production gates
    ADMIN = "admin"  # can manage data sources, users, and everything an Approver can


PERMISSIONS: dict[Role, set[str]] = {
    Role.VIEWER: {
        "incidents:read",
        "evidence:read",
    },
    Role.APPROVER: {
        "incidents:read",
        "evidence:read",
        "approvals:write",
        "incidents:create",
    },
    Role.ADMIN: {
        "incidents:read",
        "evidence:read",
        "approvals:write",
        "incidents:create",
        "data_sources:write",
        "users:write",
    },
}
