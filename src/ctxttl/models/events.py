"""Append-only lifecycle events used to project active context state."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from ctxttl.models.context import Authority, ContextOwner, ContextScope, SourceRef, as_utc, utc_now


def new_event_id() -> str:
    return f"evt_{uuid4().hex}"


class EventType(StrEnum):
    ASSERT = "assert"
    SUPERSEDE = "supersede"
    RETRACT = "retract"
    EXPIRE = "expire"


class ContextEvent(BaseModel):
    """An immutable event from which the current context state can be rebuilt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=new_event_id)
    event_type: EventType
    subject: str
    scope: ContextScope
    authority: Authority
    owner: ContextOwner
    source: SourceRef
    value: JsonValue = None
    target_context_id: str | None = None
    reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    _normalize_created_at = field_validator("created_at")(as_utc)

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        self.owner.key_for(self.scope, turn_id=self.source.turn_id)
        if self.owner.session_id != self.source.session_id:
            raise ValueError("owner and source must reference the same originating session")
        if self.event_type == EventType.ASSERT and self.target_context_id is not None:
            raise ValueError("assert events cannot target an existing context item")
        if self.event_type == EventType.SUPERSEDE and self.target_context_id is None:
            raise ValueError("supersede events require target_context_id")
        if self.event_type in {EventType.RETRACT, EventType.EXPIRE}:
            if self.target_context_id is None:
                raise ValueError(f"{self.event_type.value} events require target_context_id")
            if self.value is not None:
                raise ValueError(f"{self.event_type.value} events cannot carry a replacement value")
        return self

    @property
    def owner_key(self) -> str:
        return self.owner.key_for(self.scope, turn_id=self.source.turn_id)
