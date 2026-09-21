"""Explicit, authority-safe structured context ingestion."""

import hashlib
import json
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from ctxttl.application.context_repository import (
    ApplyResult,
    ContextStateStore,
    IdempotencyRequest,
)
from ctxttl.application.context_state import ContextStateManager
from ctxttl.identity import RequestIdentity
from ctxttl.models import (
    Authority,
    ContextApplicability,
    ContextItem,
    ContextKind,
    ContextOwner,
    ContextScope,
    Retention,
    SourceRef,
)

_ALLOWED_KINDS = {
    ContextKind.FACT,
    ContextKind.DECISION,
    ContextKind.CONSTRAINT,
    ContextKind.TASK_STATE,
}
_ALLOWED_SCOPES = {
    ContextScope.TURN,
    ContextScope.SESSION,
    ContextScope.TASK,
    ContextScope.AGENT,
    ContextScope.PROJECT,
    ContextScope.USER,
}


class StructuredContextInput(BaseModel):
    """Trusted request shape; authority and retention are intentionally not client-controlled."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ContextKind
    scope: ContextScope = ContextScope.SESSION
    applicability: ContextApplicability | None = None
    subject: str = Field(min_length=1, max_length=256)
    value: JsonValue
    priority: float = Field(default=0.7, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    turn_id: str | None = Field(default=None, min_length=1, max_length=128)
    supersedes: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    expires_at: AwareDatetime | None = None
    ttl_turns: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_supported_dimensions(self) -> Self:
        if self.kind not in _ALLOWED_KINDS:
            allowed = ", ".join(sorted(kind.value for kind in _ALLOWED_KINDS))
            raise ValueError(f"structured ingestion kind must be one of: {allowed}")
        if self.scope not in _ALLOWED_SCOPES:
            allowed = ", ".join(sorted(scope.value for scope in _ALLOWED_SCOPES))
            raise ValueError(f"structured ingestion scope must be one of: {allowed}")
        if self.expires_at is not None and self.ttl_turns is not None:
            raise ValueError("expires_at and ttl_turns are mutually exclusive lease policies")
        if self.applicability == ContextApplicability.CURRENT_TURN:
            if self.scope != ContextScope.TURN:
                raise ValueError("current_turn applicability requires turn scope")
            if self.expires_at is not None or self.ttl_turns is not None:
                raise ValueError("current_turn applicability cannot declare an additional lease")
        if self.scope == ContextScope.TURN and self.applicability not in {
            None,
            ContextApplicability.CURRENT_TURN,
        }:
            raise ValueError("turn scope requires current_turn applicability")
        return self


class StructuredContextIngestionService:
    """Translate explicit client assertions into lifecycle-managed Context IR."""

    def __init__(self, store: ContextStateStore) -> None:
        self._manager = ContextStateManager(store)

    async def ingest(
        self,
        request: StructuredContextInput,
        identity: RequestIdentity,
        *,
        request_id: str | None = None,
    ) -> ApplyResult:
        owner = ContextOwner(
            session_id=identity.session_id,
            user_id=identity.user_id,
            task_id=identity.task_id,
            agent_id=identity.agent_id,
            project_id=identity.project_id,
        )
        turn_id = self._effective_turn_id(request.turn_id, identity)
        self._validate_identity_for_scope(request.scope, owner, turn_id)
        if request.ttl_turns is not None and turn_id is None:
            raise StructuredIngestionError("ttl_turns requires X-CtxTTL-Turn-ID")
        applicability = request.applicability or (
            ContextApplicability.CURRENT_TURN
            if request.scope == ContextScope.TURN
            else ContextApplicability.SELECTIVE
        )
        item = ContextItem(
            kind=request.kind,
            scope=request.scope,
            retention=(
                Retention.EPHEMERAL
                if applicability == ContextApplicability.CURRENT_TURN
                else (
                    Retention.LEASED
                    if request.expires_at is not None or request.ttl_turns is not None
                    else Retention.PERSISTENT
                )
            ),
            applicability=applicability,
            authority=Authority.EXPLICIT_USER,
            subject=request.subject,
            value=request.value,
            priority=request.priority,
            confidence=request.confidence,
            owner=owner,
            source=SourceRef(session_id=identity.session_id, turn_id=turn_id),
            metadata=request.metadata,
            expires_at=request.expires_at,
            ttl_turns=request.ttl_turns,
        )
        if request.supersedes is not None:
            return await self._manager.supersede(
                request.supersedes,
                item,
                reason=request.reason,
                idempotency=self._idempotency(request, identity, request_id),
            )
        return await self._manager.assert_item(
            item,
            reason=request.reason,
            idempotency=self._idempotency(request, identity, request_id),
        )

    @staticmethod
    def _validate_identity_for_scope(
        scope: ContextScope,
        owner: ContextOwner,
        turn_id: str | None,
    ) -> None:
        if scope == ContextScope.TURN and turn_id is None:
            raise StructuredIngestionError("turn scope requires X-CtxTTL-Turn-ID")
        if scope == ContextScope.TASK and owner.task_id is None:
            raise StructuredIngestionError("task scope requires X-CtxTTL-Task-ID")
        if scope == ContextScope.AGENT and owner.agent_id is None:
            raise StructuredIngestionError("agent scope requires X-CtxTTL-Agent-ID")
        if scope == ContextScope.PROJECT and owner.project_id is None:
            raise StructuredIngestionError("project scope requires X-CtxTTL-Project-ID")
        if scope == ContextScope.USER and owner.user_id is None:
            raise StructuredIngestionError("user scope requires X-CtxTTL-User-ID")

    @staticmethod
    def _effective_turn_id(request_turn_id: str | None, identity: RequestIdentity) -> str | None:
        if (
            identity.turn_id is not None
            and request_turn_id is not None
            and identity.turn_id != request_turn_id
        ):
            raise StructuredIngestionError("body turn_id must match X-CtxTTL-Turn-ID")
        return identity.turn_id or request_turn_id

    @staticmethod
    def _idempotency(
        request: StructuredContextInput,
        identity: RequestIdentity,
        request_id: str | None,
    ) -> IdempotencyRequest | None:
        if request_id is None:
            return None
        effective_turn_id = identity.turn_id or request.turn_id
        canonical = json.dumps(
            {
                "identity": identity.model_copy(update={"turn_id": effective_turn_id}).model_dump(
                    mode="json"
                ),
                "input": request.model_copy(update={"turn_id": effective_turn_id}).model_dump(
                    mode="json"
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return IdempotencyRequest(
            session_id=identity.session_id,
            request_id=request_id,
            request_fingerprint=hashlib.sha256(canonical).hexdigest(),
            operation="context.ingest",
        )


class StructuredIngestionError(ValueError):
    """Raised when an ingestion request lacks required ownership coordinates."""
