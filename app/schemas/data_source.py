"""Pydantic schemas for DataSource request validation and response serialization."""

import re
import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.data_source import DataSourceHealthStatus

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")

# Credentials belong in the secret manager and are referenced via `credential_reference` (§1, §2).
_SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential|authorization|bearer", re.IGNORECASE
)


def _find_sensitive_key(value: Any, path: str = "") -> str | None:
    """Returns the dotted path of the first credential-looking key in a nested config, if any."""
    if isinstance(value, dict):
        for key, child in value.items():
            here = f"{path}.{key}" if path else str(key)
            if _SENSITIVE_KEY.search(str(key)):
                return here
            found = _find_sensitive_key(child, here)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_sensitive_key(child, f"{path}[{index}]")
            if found:
                return found
    return None


def _check_no_credentials(value: dict[str, Any]) -> dict[str, Any]:
    offending = _find_sensitive_key(value)
    if offending:
        raise ValueError(
            f"configuration_json must not contain credentials (found '{offending}'); "
            "store the secret in the secret manager and set credential_reference instead"
        )
    return value


def _validate_identifier(value: str, label: str) -> str:
    value = value.strip().lower()
    if not _IDENTIFIER.match(value):
        raise ValueError(f"{label} may only contain lowercase letters, digits, '_', '-' and '.'")
    return value


class DataSourceBase(BaseModel):
    name: str = Field(..., example="prod-payments-db", min_length=1, max_length=255)
    environment: str = Field(..., example="production", min_length=2, max_length=50)
    configuration_json: dict[str, Any] = Field(
        ...,
        description="Connection details such as host, namespace or log paths. Must NOT contain credentials.",
        example={"namespace": "payments", "log_paths": ["/var/log/app.log"]},
    )
    credential_reference: str | None = Field(
        default=None,
        max_length=255,
        description="Opaque pointer into the secret manager; the credential value itself is never stored or returned.",
        example="vault:secret/data/payments-db",
    )
    is_enabled: bool = True

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("environment")
    @classmethod
    def _normalize_environment(cls, value: str) -> str:
        return _validate_identifier(value, "environment")

    @field_validator("configuration_json")
    @classmethod
    def _reject_credentials(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_no_credentials(value)


def _normalize_capabilities(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in values:
        seen[_validate_identifier(item, "capability")] = None
    return list(seen)


class DataSourceCreate(DataSourceBase):
    """Payload for POST /api/data-sources."""

    provider_type: str = Field(..., example="postgresql", min_length=2, max_length=50)
    capabilities: list[str] = Field(default_factory=list, example=["application_logs", "database_logs"])

    @field_validator("provider_type")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return _validate_identifier(value, "provider_type")

    @field_validator("capabilities")
    @classmethod
    def _normalize_caps(cls, value: list[str]) -> list[str]:
        return _normalize_capabilities(value)


class DataSourceUpdate(BaseModel):
    """Payload for PUT /api/data-sources/{id}. Omitted fields are left unchanged; provider_type is immutable."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    environment: str | None = Field(default=None, min_length=2, max_length=50)
    configuration_json: dict[str, Any] | None = None
    credential_reference: str | None = Field(
        default=None, max_length=255, description="Send null explicitly to clear; omit to leave unchanged."
    )
    is_enabled: bool | None = None
    capabilities: list[str] | None = Field(
        default=None, description="When provided, replaces the full set of capabilities."
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("environment")
    @classmethod
    def _normalize_environment(cls, value: str | None) -> str | None:
        return None if value is None else _validate_identifier(value, "environment")

    @field_validator("configuration_json")
    @classmethod
    def _reject_credentials(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _check_no_credentials(value)

    @field_validator("capabilities")
    @classmethod
    def _normalize_caps(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _normalize_capabilities(value)


class DataSourceCapabilityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    capability: str
    is_enabled: bool


class DataSourceRead(BaseModel):
    """Response model. The credential reference is deliberately not exposed, only whether one is set."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    provider_type: str
    environment: str
    configuration_json: dict[str, Any]
    has_credential: bool
    is_enabled: bool
    capabilities: list[DataSourceCapabilityRead]
    created_at: datetime
    updated_at: datetime


class DataSourceTestResult(BaseModel):
    """Response model for POST /api/data-sources/{id}/test."""

    model_config = ConfigDict(from_attributes=True)

    status: DataSourceHealthStatus
    last_checked_at: datetime
    response_time_ms: int | None = None
    error_message: str | None = None
