"""Environment-backed configuration for the standalone MCP process."""

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    """Settings loaded from ``CTXTTL_MCP_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CTXTTL_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8766, ge=1, le=65535)
    path: str = "/mcp"
    core_url: str = "http://127.0.0.1:8765"
    core_bearer_token: SecretStr | None = None
    bearer_token: SecretStr | None = None
    timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    log_level: str = "INFO"

    @field_validator("core_bearer_token", "bearer_token", mode="before")
    @classmethod
    def empty_secret_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("core_url")
    @classmethod
    def normalize_core_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("core_url must use http:// or https://")
        return normalized

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        if not value.startswith("/") or value == "/":
            raise ValueError("path must start with '/' and name a non-root endpoint")
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("unsupported log level")
        return normalized

    @model_validator(mode="after")
    def require_authentication_for_non_loopback_bind(self) -> "MCPSettings":
        if self.host not in {"127.0.0.1", "::1", "localhost"} and self.bearer_token is None:
            raise ValueError("bearer_token is required when host is not loopback")
        return self
