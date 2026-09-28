"""Authentication and Token issuance endpoints."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.user import User
from app.schemas.user import TokenResponse, UserLoginRequest
from app.security.auth import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login & receive JWT Access Token",
    description="Authenticates user credentials and issues a signed JWT token containing subject and role claims.",
)
def login(
    payload: UserLoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Authenticates email and password, returning a signed bearer token."""
    query = select(User).where(User.email == payload.email)
    result = db.execute(query)
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    access_token = create_access_token(
        subject=str(user.id),
        claims={
            "role": user.role.value,
            "org_id": str(user.org_id),
            "email": user.email,
        },
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        role=user.role,
        org_id=user.org_id,
    )
