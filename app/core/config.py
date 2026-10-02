"""Core Configuration loaded from environment variables using Pydantic Settings."""

import json
import secrets
import warnings
from typing import Annotated, Any, Literal
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Deployment environment — anything other than "development" enforces strict secret validation
    APP_ENV: Literal["development", "staging", "production"] = "development"

    # Platform's own DB (read/write) — incidents, evidence, approvals, users
    APP_DB_URL: str = "postgresql+asyncpg://app_user:changeme@localhost:5432/incident_ai"

    # Read-only role used ONLY by the Database MCP server for investigation
    INVESTIGATION_DB_URL: str = "postgresql+asyncpg://incident_investigator:changeme@localhost:5432/target_app_db"

    # Secret manager
    SECRET_BACKEND: str = "vault"  # "vault" | "azure_keyvault" | "aws_secrets"
    VAULT_ADDR: str | None = None
    VAULT_TOKEN: SecretStr | None = None
    AZURE_KEYVAULT_URL: str | None = None

    # LLM Provider & Circuit Breaker
    LLM_PROVIDER: str = "azure_openai"  # "azure_openai" | "openai"
    AZURE_OPENAI_ENDPOINT: str | None = None
    AZURE_OPENAI_API_KEY: SecretStr | None = None
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4o-mini"
    OPENAI_API_KEY: SecretStr | None = None
    LLM_MAX_COST_PER_INCIDENT_USD: float = 2.00  # circuit breaker

    # Local Code MCP
    ALLOWED_REPOSITORIES: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # CORS — explicit origin allowlist (comma-separated or JSON list); empty = no cross-origin access
    CORS_ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("ALLOWED_REPOSITORIES", "CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_str_list(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("[") and v.endswith("]"):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return v
        return []

    # Kubernetes MCP
    KUBERNETES_API_URL: str | None = None
    KUBERNETES_TOKEN: SecretStr | None = None

    # Auth & RBAC
    OIDC_ISSUER: str = "https://login.microsoftonline.com/common/v2.0"
    OIDC_CLIENT_ID: str = "client-id-placeholder"
    JWT_SECRET: SecretStr | None = None  # required outside development; see _validate_jwt_secret
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    INVESTIGATION_SOFT_TIME_LIMIT_SECONDS: int = 600  # worker raises SoftTimeLimitExceeded → run marked failed
    INVESTIGATION_TIME_LIMIT_SECONDS: int = 660  # hard kill; must exceed the soft limit
    TASK_ENQUEUE_MAX_RETRIES: int = 2  # broker publish retries before /investigate returns 503

    # Observability
    OTEL_EXPORTER_ENDPOINT: str | None = None
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> "Settings":
        """Fail fast on a missing/weak JWT secret outside development (dev gets an ephemeral one)."""
        secret = self.JWT_SECRET.get_secret_value() if self.JWT_SECRET else ""
        weak = len(secret) < MIN_JWT_SECRET_LENGTH or secret.lower().startswith(("changeme", "default"))
        if not weak:
            return self
        if self.APP_ENV != "development":
            raise ValueError(
                f"JWT_SECRET must be set to a random value of at least {MIN_JWT_SECRET_LENGTH} characters "
                f"when APP_ENV='{self.APP_ENV}'"
            )
        warnings.warn(
            "JWT_SECRET is not set (or too weak); using an ephemeral secret for this process. "
            "Issued tokens will be invalid after a restart.",
            stacklevel=2,
        )
        self.JWT_SECRET = SecretStr(secrets.token_urlsafe(48))
        return self


settings = Settings()
