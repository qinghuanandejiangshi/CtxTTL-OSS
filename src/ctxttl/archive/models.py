"""Provider-neutral models for the raw conversation archive."""

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from ctxttl.models import ContextOwner
from ctxttl.models.context import as_utc, utc_now


def _canonical_message(message: dict[str, JsonValue]) -> str:
    return json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class MessageDirection(StrEnum):
    """Whether an archive occurrence entered or left the proxy."""

    INPUT = "input"
    OUTPUT = "output"


class ConversationEntry(BaseModel):
    """One request-relative message occurrence inside an ownership boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    owner: ContextOwner
    message: dict[str, JsonValue]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_trace_id: str
    request_id: str | None = None
    direction: MessageDirection = MessageDirection.INPUT
    position: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)

    _normalize_created_at = field_validator("created_at")(as_utc)

    @classmethod
    def create(
        cls,
        *,
        owner: ContextOwner,
        message: dict[str, JsonValue],
        source_trace_id: str,
        request_id: str,
        direction: MessageDirection,
        created_at: datetime | None = None,
        position: int = 0,
    ) -> Self:
        canonical = _canonical_message(message)
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        identity = f"{owner.session_id}\0{request_id}\0{direction.value}\0{position}".encode()
        entry_id = f"arc_{hashlib.sha256(identity).hexdigest()[:32]}"
        timestamp = created_at or utc_now()
        if position:
            timestamp += timedelta(microseconds=position)
        return cls(
            id=entry_id,
            owner=owner,
            message=message,
            content_hash=content_hash,
            source_trace_id=source_trace_id,
            request_id=request_id,
            direction=direction,
            position=position,
            created_at=timestamp,
        )

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        expected = hashlib.sha256(_canonical_message(self.message).encode("utf-8")).hexdigest()
        if self.content_hash != expected:
            raise ValueError("content_hash does not match the archived message")
        if self.request_id is None:
            identity = f"{self.owner.session_id}\0{self.content_hash}".encode()
        else:
            identity = (
                f"{self.owner.session_id}\0{self.request_id}\0"
                f"{self.direction.value}\0{self.position}"
            ).encode()
        expected_id = f"arc_{hashlib.sha256(identity).hexdigest()[:32]}"
        if self.id != expected_id:
            raise ValueError("archive entry ID does not match its occurrence identity")
        return self

    @property
    def searchable_text(self) -> str:
        """Flatten message JSON without interpreting provider-specific fields."""

        def collect(value: JsonValue) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [text for child in value for text in collect(child)]
            if isinstance(value, dict):
                return [text for child in value.values() for text in collect(child)]
            return []

        return " ".join(collect(self.message)).strip()

    @property
    def owner_keys(self) -> tuple[str, ...]:
        keys = [self.owner.key_for(scope) for scope in _available_scopes(self.owner)]
        return tuple(dict.fromkeys(keys))


def _available_scopes(owner: ContextOwner):
    from ctxttl.models import ContextScope

    yield ContextScope.SESSION
    if owner.task_id is not None:
        yield ContextScope.TASK
    if owner.agent_id is not None:
        yield ContextScope.AGENT
    if owner.project_id is not None:
        yield ContextScope.PROJECT
    if owner.user_id is not None:
        yield ContextScope.USER


class ArchiveSearchHit(BaseModel):
    """An FTS result with SQLite's raw BM25 rank (lower is better)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry: ConversationEntry
    rank: float
