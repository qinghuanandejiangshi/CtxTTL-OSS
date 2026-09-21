"""Provider-neutral intermediate representation for context items."""

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_context_id() -> str:
    return f"ctx_{uuid4().hex}"


def as_utc(value: datetime | None) -> datetime | None:
    """Reject ambiguous datetimes and normalize aware values to UTC."""

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must include timezone information")
    return value.astimezone(UTC)


class ContextKind(StrEnum):
    MESSAGE = "message"
    FACT = "fact"
    DECISION = "decision"
    CONSTRAINT = "constraint"
    TASK_STATE = "task_state"
    TOOL_RESULT = "tool_result"
    RAG_EVIDENCE = "rag_evidence"
    SUMMARY = "summary"


class ContextScope(StrEnum):
    TURN = "turn"
    SESSION = "session"
    TASK = "task"
    AGENT = "agent"
    PROJECT = "project"
    USER = "user"


class Retention(StrEnum):
    EPHEMERAL = "ephemeral"
    LEASED = "leased"
    PERSISTENT = "persistent"
    ARCHIVED = "archived"


class ContextApplicability(StrEnum):
    """When an active context item must or may be compiled into a request."""

    CURRENT_TURN = "current_turn"
    SELECTIVE = "selective"
    REQUIRED = "required"


class Authority(StrEnum):
    SYSTEM = "system"
    DEVELOPER = "developer"
    EXPLICIT_USER = "explicit_user"
    VERIFIED_TOOL = "verified_tool"
    RETRIEVED_SOURCE = "retrieved_source"
    ASSISTANT_INFERENCE = "assistant_inference"
    SUMMARY = "summary"


class ContextStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    RETRACTED = "retracted"


class ContextOwner(BaseModel):
    """Explicit isolation coordinates for a context item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    user_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None

    def key_for(self, scope: ContextScope, *, turn_id: str | None = None) -> str:
        if scope == ContextScope.TURN:
            if turn_id is None:
                raise ValueError("turn scope requires turn_id")
            coordinates = [scope.value, self.session_id, turn_id]
            return json.dumps(coordinates, ensure_ascii=False, separators=(",", ":"))
        if scope == ContextScope.SESSION:
            coordinates = [scope.value, self.session_id]
            return json.dumps(coordinates, ensure_ascii=False, separators=(",", ":"))
        if scope == ContextScope.TASK:
            if self.task_id is None:
                raise ValueError("task scope requires task_id")
            namespace = self.user_id or self.session_id
            coordinates = [scope.value, namespace]
            if self.project_id is not None:
                coordinates.append(self.project_id)
            coordinates.append(self.task_id)
            return json.dumps(coordinates, ensure_ascii=False, separators=(",", ":"))
        if scope == ContextScope.AGENT:
            if self.agent_id is None:
                raise ValueError("agent scope requires agent_id")
            namespace = self.user_id or self.session_id
            coordinates = [scope.value, namespace]
            if self.project_id is not None:
                coordinates.append(self.project_id)
            coordinates.append(self.agent_id)
            return json.dumps(coordinates, ensure_ascii=False, separators=(",", ":"))
        if scope == ContextScope.PROJECT:
            if self.project_id is None:
                raise ValueError("project scope requires project_id")
            namespace = self.user_id or self.session_id
            coordinates = [scope.value, namespace, self.project_id]
            return json.dumps(coordinates, ensure_ascii=False, separators=(",", ":"))
        if self.user_id is None:
            raise ValueError("user scope requires user_id")
        coordinates = [scope.value, self.user_id]
        return json.dumps(coordinates, ensure_ascii=False, separators=(",", ":"))


class SourceRef(BaseModel):
    """A traceable reference to the origin of a context item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    turn_id: str | None = None
    tool_call_id: str | None = None
    document_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)

    _normalize_timestamp = field_validator("timestamp")(as_utc)


class ContextItem(BaseModel):
    """The canonical Context IR item passed between runtime components."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=new_context_id)
    kind: ContextKind
    scope: ContextScope
    retention: Retention
    applicability: ContextApplicability = ContextApplicability.SELECTIVE
    authority: Authority
    status: ContextStatus = ContextStatus.ACTIVE
    subject: str | None = None
    value: JsonValue = None
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)
    owner: ContextOwner
    source: SourceRef
    source_event_id: str | None = None
    supersedes: str | None = None
    token_cost: int | None = Field(default=None, ge=0)
    ttl_turns: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    _normalize_datetimes = field_validator("created_at", "expires_at")(as_utc)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        self.owner.key_for(self.scope, turn_id=self.source.turn_id)
        if self.owner.session_id != self.source.session_id:
            raise ValueError("owner and source must reference the same originating session")
        has_lease = self.ttl_turns is not None or self.expires_at is not None
        if self.retention == Retention.LEASED and not has_lease:
            raise ValueError("leased context requires ttl_turns or expires_at")
        if self.retention != Retention.LEASED and has_lease:
            raise ValueError("ttl_turns and expires_at are only valid for leased context")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        if self.supersedes == self.id:
            raise ValueError("a context item cannot supersede itself")
        if self.applicability == ContextApplicability.CURRENT_TURN:
            if self.scope != ContextScope.TURN:
                raise ValueError("current_turn applicability requires turn scope")
            if self.retention != Retention.EPHEMERAL:
                raise ValueError("current_turn applicability requires ephemeral retention")
        if self.applicability == ContextApplicability.REQUIRED and self.authority in {
            Authority.RETRIEVED_SOURCE,
            Authority.ASSISTANT_INFERENCE,
            Authority.SUMMARY,
        }:
            raise ValueError("untrusted context cannot declare required applicability")
        return self

    @property
    def owner_key(self) -> str:
        return self.owner.key_for(self.scope, turn_id=self.source.turn_id)
