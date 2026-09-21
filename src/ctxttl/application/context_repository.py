"""Application-owned persistence contract for context state."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ctxttl.models import ContextEvent, ContextItem, ContextStatus


class ContextStoreError(RuntimeError):
    """Base class for context persistence failures."""


class ContextNotFound(ContextStoreError):
    """Raised when a requested context item does not exist."""


class ContextConflict(ContextStoreError):
    """Raised when a lifecycle transition violates current state."""


class EventConflict(ContextStoreError):
    """Raised when an event ID is reused with different content."""


class RequestConflict(ContextStoreError):
    """Raised when an idempotency key is reused for a different mutation."""


@dataclass(frozen=True, slots=True)
class ApplyResult:
    event: ContextEvent
    item: ContextItem | None
    applied: bool


@dataclass(frozen=True, slots=True)
class IdempotencyRequest:
    session_id: str
    request_id: str
    request_fingerprint: str
    operation: str


class ContextStateStore(Protocol):
    """Atomic event store and active-state projection contract."""

    async def initialize(self) -> None: ...

    async def apply(
        self,
        event: ContextEvent,
        item: ContextItem | None = None,
    ) -> ApplyResult: ...

    async def apply_idempotent(
        self,
        event: ContextEvent,
        item: ContextItem | None,
        *,
        session_id: str,
        request_id: str,
        request_fingerprint: str,
        operation: str,
    ) -> ApplyResult: ...

    async def get_item(self, context_id: str) -> ContextItem | None: ...

    async def list_active(self, owner_keys: Sequence[str]) -> list[ContextItem]: ...

    async def list_items(
        self,
        owner_keys: Sequence[str],
        *,
        status: ContextStatus | None = None,
        limit: int = 100,
    ) -> list[ContextItem]: ...

    async def observe_turn(
        self,
        owner_keys: Sequence[str],
        *,
        session_id: str,
        turn_id: str,
    ) -> list[str]: ...

    async def list_events(self, session_id: str | None = None) -> list[ContextEvent]: ...

    async def aclose(self) -> None: ...
