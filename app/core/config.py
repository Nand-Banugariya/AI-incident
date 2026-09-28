"""Core Configuration loaded from environment variables using Pydantic Settings."""

import json
from typing import Annotated, Any
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
    JWT_SECRET: SecretStr = SecretStr("default_insecure_jwt_secret_change_in_production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"

    # Observability
    OTEL_EXPORTER_ENDPOINT: str | None = None
    LOG_LEVEL: str = "INFO"


settings = Settings()
