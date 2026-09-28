"""Pydantic schemas for User authentication and profile data."""

import uuid
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.security.rbac import Role


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    org_id: uuid.UUID


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: Role
    org_id: uuid.UUID
    is_active: bool
