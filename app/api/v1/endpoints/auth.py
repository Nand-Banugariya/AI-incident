"""Authentication and Token issuance endpoints."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.user import User
from app.schemas.user import TokenResponse, UserLoginRequest, UserRegisterRequest
from app.security.auth import create_access_token, verify_password
from app.services.auth_service import AuthService, RegistrationConflict

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _issue_token(user: User) -> TokenResponse:
    """Builds the signed bearer token response for an authenticated user."""
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


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register & receive JWT Access Token",
    description="Creates a new organization with the caller as its admin, and issues a signed JWT token.",
)
def register(
    payload: UserRegisterRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Registers a new organization + admin user, returning a signed bearer token."""
    try:
        user = AuthService.register(db=db, payload=payload)
    except RegistrationConflict as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err))
    return _issue_token(user)


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
    query = select(User).where(func.lower(User.email) == payload.email.lower())
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

    return _issue_token(user)
