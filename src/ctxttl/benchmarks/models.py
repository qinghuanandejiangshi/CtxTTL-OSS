"""Validated benchmark dataset and report models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ctxttl.identity import RequestIdentity
from ctxttl.models import (
    Authority,
    ContextApplicability,
    ContextItem,
    ContextKind,
    ContextOwner,
    ContextScope,
    ContextStatus,
    Retention,
    SourceRef,
)


class StrategyName(StrEnum):
    FULL_HISTORY = "full_history"
    ACTIVE_HISTORY = "active_history"
    SLIDING_WINDOW = "sliding_window"
    RUNNING_SUMMARY = "running_summary"
    RETRIEVAL_ONLY = "retrieval_only"
    CTXTTL = "ctxttl"


class BenchmarkContext(BaseModel):
    """Compact, portable Context IR fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: ContextKind = ContextKind.FACT
    scope: ContextScope = ContextScope.TASK
    retention: Retention = Retention.PERSISTENT
    authority: Authority = Authority.EXPLICIT_USER
    applicability: ContextApplicability | None = None
    status: ContextStatus = ContextStatus.ACTIVE
    subject: str | None = None
    value: JsonValue = None
    priority: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    created_at: datetime
    session_id: str | None = None
    user_id: str | None = None
    task_id: str | None = None
    turn_id: str | None = None
    supersedes: str | None = None
    source_message: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_source_message(self) -> Self:
        if self.source_message is not None:
            role = self.source_message.get("role")
            content = self.source_message.get("content")
            if not isinstance(role, str) or not role:
                raise ValueError("source_message.role must be a non-empty string")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("source_message.content must be non-empty text")
        return self

    def to_item(self, identity: RequestIdentity) -> ContextItem:
        session_id = self.session_id or identity.session_id
        owner = ContextOwner(
            session_id=session_id,
            user_id=self.user_id if self.user_id is not None else identity.user_id,
            task_id=self.task_id if self.task_id is not None else identity.task_id,
            agent_id=identity.agent_id,
            project_id=identity.project_id,
        )
        return ContextItem(
            id=self.id,
            kind=self.kind,
            scope=self.scope,
            retention=self.retention,
            applicability=self.applicability or ContextApplicability.SELECTIVE,
            authority=self.authority,
            status=self.status,
            subject=self.subject,
            value=self.value,
            priority=self.priority,
            confidence=self.confidence,
            created_at=self.created_at,
            owner=owner,
            source=SourceRef(
                session_id=session_id,
                turn_id=self.turn_id,
                timestamp=self.created_at,
            ),
            supersedes=self.supersedes,
        )


class BenchmarkCase(BaseModel):
    """One model-free context selection and replay case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    identity: RequestIdentity
    messages: tuple[dict[str, Any], ...]
    context: tuple[BenchmarkContext, ...] = ()
    required_context_ids: frozenset[str] = frozenset()
    forbidden_context_ids: frozenset[str] = frozenset()
    summary: str | None = None
    summary_context_ids: frozenset[str] = frozenset()
    target_tokens: int = Field(default=2_000, ge=1)
    max_tokens: int = Field(default=4_000, ge=1)
    recent_turn_reserve: int = Field(default=2, ge=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_case(self) -> Self:
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens cannot exceed max_tokens")
        known_ids = {item.id for item in self.context}
        if len(known_ids) != len(self.context):
            raise ValueError("context fixture IDs must be unique")
        expected_ids = (
            self.required_context_ids | self.forbidden_context_ids | self.summary_context_ids
        )
        if not expected_ids <= known_ids:
            raise ValueError("required and forbidden IDs must reference context fixtures")
        if self.required_context_ids & self.forbidden_context_ids:
            raise ValueError("required and forbidden context IDs must be disjoint")
        if self.summary_context_ids and self.summary is None:
            raise ValueError("summary_context_ids require a precomputed summary")
        for item in self.context:
            item.to_item(self.identity)
        return self

    def context_items(self) -> tuple[ContextItem, ...]:
        return tuple(item.to_item(self.identity) for item in self.context)


class StrategyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: StrategyName
    selected_context_ids: tuple[str, ...]
    messages: tuple[dict[str, Any], ...]
    estimated_tokens: int = Field(ge=0)


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    strategy: StrategyName
    selected_context_ids: tuple[str, ...]
    estimated_tokens: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    required_recall: float = Field(ge=0, le=1)
    forbidden_inclusion_rate: float = Field(ge=0, le=1)
    superseded_inclusion_rate: float = Field(ge=0, le=1)
    selection_success: bool
    stable: bool


class StrategySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: StrategyName
    case_count: int = Field(ge=1)
    selection_success_rate: float = Field(ge=0, le=1)
    mean_required_recall: float = Field(ge=0, le=1)
    mean_forbidden_inclusion_rate: float = Field(ge=0, le=1)
    mean_superseded_inclusion_rate: float = Field(ge=0, le=1)
    mean_estimated_tokens: float = Field(ge=0)
    mean_duration_ms: float = Field(ge=0)
    stability_rate: float = Field(ge=0, le=1)
    estimated_input_cost: float | None = Field(default=None, ge=0)


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_case_count: int = Field(ge=1)
    summaries: tuple[StrategySummary, ...]
    cases: tuple[CaseResult, ...]
    answer_accuracy: None = None
    note: str = (
        "Model-free harness: selection metrics are measured; answer accuracy requires an external "
        "model and judge."
    )
