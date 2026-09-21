"""Privacy-aware, persistable compilation trace models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _trace_id() -> str:
    return f"trc_{uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("trace timestamps must include timezone information")
    return value.astimezone(UTC)


class TraceDecision(BaseModel):
    """Serializable form of one compiler selection decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    candidate_id: str
    candidate_type: str
    included: bool
    reason: str
    token_cost: int = Field(ge=1)
    utility: float | None = None


class PreCompilationExclusion(BaseModel):
    """Messages intentionally removed by an Agent before context compilation."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    message_count: int = Field(default=0, ge=0)
    token_count: int = Field(default=0, ge=0)
    message_counts_by_reason: dict[str, int] = Field(default_factory=dict)
    tokens_by_reason: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reason_totals(self) -> Self:
        if any(not key or value < 0 for key, value in self.message_counts_by_reason.items()):
            raise ValueError("message exclusion reason counts must be non-negative")
        if any(not key or value < 0 for key, value in self.tokens_by_reason.items()):
            raise ValueError("token exclusion reason counts must be non-negative")
        if sum(self.message_counts_by_reason.values()) != self.message_count:
            raise ValueError("message exclusion reason counts must equal message_count")
        if sum(self.tokens_by_reason.values()) != self.token_count:
            raise ValueError("token exclusion reason counts must equal token_count")
        return self


class TokenSavingsLedger(BaseModel):
    """Non-overlapping token accounting from Agent input to provider input."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    pre_compilation_excluded_tokens: int = Field(default=0, ge=0)
    source_transcript_tokens: int = Field(ge=0)
    candidate_context_tokens: int = Field(ge=0)
    selected_transcript_tokens: int = Field(ge=0)
    selected_context_tokens: int = Field(ge=0)
    message_budget_excluded_tokens: int = Field(ge=0)
    inactive_context_tokens: int = Field(ge=0)
    redundant_context_tokens: int = Field(ge=0)
    low_relevance_context_tokens: int = Field(default=0, ge=0)
    context_budget_excluded_tokens: int = Field(ge=0)
    provider_input_tokens: int = Field(ge=0)
    removed_before_provider_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_accounting(self) -> Self:
        transcript_total = self.selected_transcript_tokens + self.message_budget_excluded_tokens
        if transcript_total != self.source_transcript_tokens:
            raise ValueError("transcript token ledger does not balance")
        context_total = (
            self.selected_context_tokens
            + self.inactive_context_tokens
            + self.redundant_context_tokens
            + self.low_relevance_context_tokens
            + self.context_budget_excluded_tokens
        )
        if context_total != self.candidate_context_tokens:
            raise ValueError("context token ledger does not balance")
        if (
            self.selected_transcript_tokens + self.selected_context_tokens
            != self.provider_input_tokens
        ):
            raise ValueError("selected token ledger does not match provider input")
        removed = (
            self.pre_compilation_excluded_tokens
            + self.message_budget_excluded_tokens
            + self.inactive_context_tokens
            + self.redundant_context_tokens
            + self.low_relevance_context_tokens
            + self.context_budget_excluded_tokens
        )
        if removed != self.removed_before_provider_tokens:
            raise ValueError("removed token ledger does not balance")
        return self


class CompilationTiming(BaseModel):
    """Wall-clock segments inside context compilation."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    context_state_ms: float = Field(default=0, ge=0)
    history_retrieval_ms: float = Field(default=0, ge=0)
    compiler_ms: float = Field(default=0, ge=0)
    total_ms: float = Field(default=0, ge=0)


class ExecutionOutcome(StrEnum):
    """Terminal state of the provider-facing portion of a proxy request."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    UPSTREAM_ERROR = "upstream_error"


class ExecutionTrace(BaseModel):
    """Persisted network and streaming timing linked to one compilation trace."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    trace_id: str = Field(pattern=r"^trc_[0-9a-f]+$")
    session_id: str
    request_id: str | None = None
    protocol: str = Field(default="chat_completions", min_length=1)
    streaming: bool
    outcome: ExecutionOutcome
    status_code: int | None = Field(default=None, ge=100, le=599)
    started_at: datetime
    completed_at: datetime
    compilation_duration_ms: float = Field(ge=0)
    upstream_call_duration_ms: float | None = Field(default=None, ge=0)
    stream_duration_ms: float | None = Field(default=None, ge=0)
    proxy_total_duration_ms: float = Field(ge=0)
    provider_input_tokens: int | None = Field(default=None, ge=0)
    provider_cached_input_tokens: int | None = Field(default=None, ge=0)
    provider_output_tokens: int | None = Field(default=None, ge=0)

    _normalize_started_at = field_validator("started_at")(_as_utc)
    _normalize_completed_at = field_validator("completed_at")(_as_utc)

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("execution completion cannot precede its start")
        if self.outcome == ExecutionOutcome.COMPLETED and self.status_code is None:
            raise ValueError("completed execution requires a provider status code")
        if not self.streaming and self.stream_duration_ms is not None:
            raise ValueError("buffered execution cannot have stream duration")
        return self


class CompilationTrace(BaseModel):
    """Metadata required to audit one context compilation."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    id: str = Field(default_factory=_trace_id)
    created_at: datetime = Field(default_factory=_utc_now)
    session_id: str
    user_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    model: str | None = None
    streaming: bool
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_message_count: int = Field(ge=0)
    compiled_message_count: int = Field(ge=0)
    candidate_context_count: int = Field(ge=0)
    retrieved_context_count: int = Field(default=0, ge=0)
    selected_retrieved_context_ids: tuple[str, ...] = ()
    retrieval_ranks: dict[str, float] = Field(default_factory=dict)
    source_message_tokens: int = Field(ge=0)
    compiled_message_tokens: int = Field(ge=0)
    output_token_reserve: int = Field(ge=0)
    target_input_tokens: int = Field(ge=1)
    max_input_tokens: int = Field(ge=1)
    recent_turn_reserve: int | None = Field(default=None, ge=1)
    compiler_revision: str | None = None
    compilation_duration_ms: float = Field(ge=0)
    compilation_timing: CompilationTiming | None = None
    pre_compilation_exclusion: PreCompilationExclusion = Field(
        default_factory=PreCompilationExclusion
    )
    token_ledger: TokenSavingsLedger | None = None
    selected_context_ids: tuple[str, ...] = ()
    decisions: tuple[TraceDecision, ...] = ()
    source_messages: tuple[dict[str, Any], ...] | None = None
    candidate_context: tuple[dict[str, Any], ...] | None = None

    _normalize_created_at = field_validator("created_at")(_as_utc)

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        if self.target_input_tokens > self.max_input_tokens:
            raise ValueError("target_input_tokens cannot exceed max_input_tokens")
        return self
