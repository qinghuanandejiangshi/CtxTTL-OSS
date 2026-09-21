"""Runtime configuration for CtxTTL."""

from enum import StrEnum

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingSessionBehavior(StrEnum):
    """How the proxy behaves when no explicit session identity is present."""

    REJECT = "reject"
    PASSTHROUGH = "passthrough"


class Settings(BaseSettings):
    """CtxTTL settings loaded from ``CTXTTL_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CTXTTL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    database_url: str = "sqlite:///./data/ctxttl.db"
    upstream_base_url: str = "https://api.openai.com/v1"
    upstream_api_key: SecretStr | None = None
    upstream_timeout_seconds: float = Field(default=60.0, gt=0)
    upstream_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    session_header: str = "X-CtxTTL-Session-ID"
    user_header: str = "X-CtxTTL-User-ID"
    task_header: str = "X-CtxTTL-Task-ID"
    agent_header: str = "X-CtxTTL-Agent-ID"
    project_header: str = "X-CtxTTL-Project-ID"
    turn_header: str = "X-CtxTTL-Turn-ID"
    request_header: str = "X-CtxTTL-Request-ID"
    history_complete_header: str = "X-CtxTTL-History-Complete"
    missing_session_behavior: MissingSessionBehavior = MissingSessionBehavior.REJECT
    target_context_tokens: int = Field(default=24_000, ge=1)
    max_context_tokens: int = Field(default=64_000, ge=1)
    recent_turn_reserve: int = Field(default=6, ge=1)
    trace_capture_content: bool = False
    archive_enabled: bool = True
    archive_retention_days: int | None = Field(default=30, ge=1)
    history_retrieval_enabled: bool = True
    history_retrieval_limit: int = Field(default=2, ge=1, le=100)
    history_reference_retrieval_limit: int = Field(default=6, ge=1, le=100)
    response_capture_max_bytes: int = Field(default=2_000_000, ge=1)
    log_level: str = "INFO"

    @field_validator("upstream_api_key", mode="before")
    @classmethod
    def empty_api_key_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("archive_retention_days", mode="before")
    @classmethod
    def empty_archive_retention_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("upstream_base_url")
    @classmethod
    def normalize_upstream_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("upstream_base_url must use http:// or https://")
        return normalized

    @field_validator(
        "session_header",
        "user_header",
        "task_header",
        "agent_header",
        "project_header",
        "turn_header",
        "request_header",
        "history_complete_header",
    )
    @classmethod
    def validate_header_name(cls, value: str) -> str:
        if not value or any(character.isspace() for character in value):
            raise ValueError("identity header names must be non-empty and contain no spaces")
        return value

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("unsupported log level")
        return normalized

    @model_validator(mode="after")
    def validate_token_budget(self) -> "Settings":
        if self.target_context_tokens > self.max_context_tokens:
            raise ValueError("target_context_tokens cannot exceed max_context_tokens")
        if self.upstream_connect_timeout_seconds > self.upstream_timeout_seconds:
            raise ValueError(
                "upstream_connect_timeout_seconds cannot exceed upstream_timeout_seconds"
            )
        if self.history_reference_retrieval_limit < self.history_retrieval_limit:
            raise ValueError(
                "history_reference_retrieval_limit cannot be lower than history_retrieval_limit"
            )
        return self
