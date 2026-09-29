"""Service layer handling user registration and organization bootstrap."""

import logging
import re
import uuid
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.models.user import User
from app.schemas.user import UserRegisterRequest
from app.security.auth import get_password_hash
from app.security.rbac import Role

logger = logging.getLogger(__name__)


class RegistrationConflict(Exception):
    """Raised when the email or organization is already taken."""


class AuthService:
    """Encapsulates registration workflows."""

    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:90]
        return slug or f"org-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def register(db: Session, payload: UserRegisterRequest) -> User:
        """Creates a new organization and its first user with the ADMIN role."""
        email = payload.email.lower()

        if db.scalar(select(User.id).where(func.lower(User.email) == email)):
            raise RegistrationConflict("A user with this email already exists")

        if db.scalar(
            select(Organization.id).where(func.lower(Organization.name) == payload.organization_name.lower())
        ):
            raise RegistrationConflict("An organization with this name already exists")

        slug = AuthService._slugify(payload.organization_name)
        if db.scalar(select(Organization.id).where(Organization.slug == slug)):
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        organization = Organization(name=payload.organization_name, slug=slug)
        db.add(organization)
        db.flush()

        user = User(
            email=email,
            full_name=payload.full_name.strip() if payload.full_name else None,
            hashed_password=get_password_hash(payload.password),
            role=Role.ADMIN,
            org_id=organization.id,
        )
        db.add(user)

        try:
            db.commit()
        except IntegrityError:
            # Concurrent registration grabbed the same email/org name between check and insert
            db.rollback()
            raise RegistrationConflict("A user or organization with these details already exists")

        db.refresh(user)
        logger.info("Registered user %s as admin of new organization %s", user.id, organization.id)
        return user
