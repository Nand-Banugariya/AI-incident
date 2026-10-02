"""FastAPI Dependency Injection providers: DB sessions, user resolution, and security gates."""

import logging
import uuid
from typing import Annotated, Callable
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.approval import Approval, ApprovalStage, ApprovalStatus
from app.models.incident import Incident
from app.models.user import User
from app.security.auth import decode_access_token
from app.security.rbac import PERMISSIONS, Role

logger = logging.getLogger(__name__)

# Security scheme for JWT Bearer
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Validates the Bearer token and returns the authenticated User entity."""
    if not auth or not auth.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(auth.credentials)
        user_id_str: str | None = payload.get("sub")
        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is missing subject identifier",
            )
        user_id = uuid.UUID(user_id_str)
    except (jwt.PyJWTError, ValueError) as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired authentication token: {err}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with token no longer exists or is inactive",
        )
    return user


def require_permission(permission: str) -> Callable:
    """Dependency factory enforcing RBAC permission checks."""
    def permission_checker(
        current_user: Annotated[User, Depends(get_current_user)]
    ) -> User:
        user_permissions = PERMISSIONS.get(current_user.role, set())
        if permission not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: '{permission}'",
            )
        return current_user

    permission_checker.required_permission = permission  # lets tests assert which permission a route enforces
    return permission_checker


def require_role(*roles: Role) -> Callable:
    """Dependency factory restricting an endpoint to specific roles (e.g. [admin] routes in §8)."""
    def role_checker(
        current_user: Annotated[User, Depends(get_current_user)]
    ) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(r.value for r in roles)}",
            )
        return current_user

    return role_checker


def require_approval(stage: ApprovalStage) -> Callable:
    """Second, independent enforcement point for every write-triggering endpoint (§4, §8).

    Passes only if the NEWEST approval row for this incident+stage is APPROVED. Newest-wins means a later
    rejection or a freshly opened (pending) request revokes an earlier approval: the check fails closed.
    Re-checks the caller's org itself rather than trusting that another dependency did, so it is safe
    to use on its own. Pair it with `require_permission(...)` — it does not check roles.
    """
    def approval_checker(
        incident_id: uuid.UUID,
        current_user: Annotated[User, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> Approval:
        in_scope = db.scalar(
            select(Incident.id).where(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        )
        if in_scope is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Incident with ID '{incident_id}' not found",
            )

        approval = db.scalar(
            select(Approval)
            .where(Approval.incident_id == incident_id, Approval.stage == stage)
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        if approval is None or approval.status != ApprovalStatus.APPROVED:
            logger.warning(
                "Approval gate denied: incident=%s stage=%s latest_status=%s user=%s",
                incident_id, stage.value, approval.status.value if approval else "none", current_user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No approved {stage.value} approval for this incident",
            )
        return approval

    approval_checker.required_stage = stage  # lets tests assert which gate a route sits behind
    return approval_checker
