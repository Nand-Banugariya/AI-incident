"""Pydantic schemas for User authentication and profile data."""

import uuid
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.security.rbac import Role


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)


class UserRegisterRequest(BaseModel):
    """Payload for POST /api/auth/register — creates a new organization with the caller as admin."""
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str | None = Field(default=None, max_length=255)
    organization_name: str = Field(..., min_length=2, max_length=255)

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return value

    @field_validator("organization_name")
    @classmethod
    def organization_name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Organization name must be at least 2 characters")
        return value


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
