"""Lifecycle use cases for append-only context state."""

from ctxttl.application.context_repository import (
    ApplyResult,
    ContextConflict,
    ContextNotFound,
    ContextStateStore,
    IdempotencyRequest,
)
from ctxttl.models import (
    Authority,
    ContextEvent,
    ContextItem,
    ContextOwner,
    ContextStatus,
    EventType,
    SourceRef,
)


class ContextStateManager:
    """Construct valid lifecycle events and persist them atomically."""

    def __init__(self, store: ContextStateStore) -> None:
        self._store = store

    async def assert_item(
        self,
        item: ContextItem,
        *,
        reason: str | None = None,
        idempotency: IdempotencyRequest | None = None,
    ) -> ApplyResult:
        self._validate_new_item(item)
        event = ContextEvent(
            event_type=EventType.ASSERT,
            subject=item.subject or item.id,
            scope=item.scope,
            authority=item.authority,
            owner=item.owner,
            source=item.source,
            value=item.value,
            reason=reason,
        )
        persisted_item = item.model_copy(update={"source_event_id": event.id})
        return await self._apply(event, persisted_item, idempotency)

    async def supersede(
        self,
        target_context_id: str,
        replacement: ContextItem,
        *,
        reason: str | None = None,
        idempotency: IdempotencyRequest | None = None,
    ) -> ApplyResult:
        self._validate_new_item(replacement)
        target = await self._required_item(target_context_id)
        if target.status != ContextStatus.ACTIVE and idempotency is None:
            raise ContextConflict("only active context can be superseded")
        if replacement.owner_key != target.owner_key:
            raise ContextConflict("replacement must use the same ownership boundary")
        if replacement.scope != target.scope or replacement.subject != target.subject:
            raise ContextConflict("replacement must preserve target scope and subject")

        event = ContextEvent(
            event_type=EventType.SUPERSEDE,
            subject=replacement.subject or replacement.id,
            scope=replacement.scope,
            authority=replacement.authority,
            owner=replacement.owner,
            source=replacement.source,
            value=replacement.value,
            target_context_id=target.id,
            reason=reason,
        )
        persisted_item = replacement.model_copy(
            update={"source_event_id": event.id, "supersedes": target.id}
        )
        return await self._apply(event, persisted_item, idempotency)

    async def retract(
        self,
        target_context_id: str,
        *,
        owner: ContextOwner,
        source: SourceRef,
        authority: Authority,
        reason: str | None = None,
    ) -> ApplyResult:
        return await self._end_lifecycle(
            EventType.RETRACT,
            target_context_id,
            owner=owner,
            source=source,
            authority=authority,
            reason=reason,
        )

    async def expire(
        self,
        target_context_id: str,
        *,
        owner: ContextOwner,
        source: SourceRef,
        authority: Authority,
        reason: str | None = None,
    ) -> ApplyResult:
        return await self._end_lifecycle(
            EventType.EXPIRE,
            target_context_id,
            owner=owner,
            source=source,
            authority=authority,
            reason=reason,
        )

    async def _end_lifecycle(
        self,
        event_type: EventType,
        target_context_id: str,
        *,
        owner: ContextOwner,
        source: SourceRef,
        authority: Authority,
        reason: str | None,
    ) -> ApplyResult:
        target = await self._required_item(target_context_id)
        if target.status != ContextStatus.ACTIVE:
            raise ContextConflict("only active context can end its lifecycle")
        if owner.key_for(target.scope, turn_id=source.turn_id) != target.owner_key:
            raise ContextConflict("lifecycle event must use the same ownership boundary")
        event = ContextEvent(
            event_type=event_type,
            subject=target.subject or target.id,
            scope=target.scope,
            authority=authority,
            owner=owner,
            source=source,
            target_context_id=target.id,
            reason=reason,
        )
        return await self._store.apply(event)

    async def _required_item(self, context_id: str) -> ContextItem:
        item = await self._store.get_item(context_id)
        if item is None:
            raise ContextNotFound(f"context item not found: {context_id}")
        return item

    async def _apply(
        self,
        event: ContextEvent,
        item: ContextItem | None,
        idempotency: IdempotencyRequest | None,
    ) -> ApplyResult:
        if idempotency is None:
            return await self._store.apply(event, item)
        return await self._store.apply_idempotent(
            event,
            item,
            session_id=idempotency.session_id,
            request_id=idempotency.request_id,
            request_fingerprint=idempotency.request_fingerprint,
            operation=idempotency.operation,
        )

    @staticmethod
    def _validate_new_item(item: ContextItem) -> None:
        if item.status != ContextStatus.ACTIVE:
            raise ContextConflict("new context must be active")
        if item.source_event_id is not None or item.supersedes is not None:
            raise ContextConflict("event linkage is managed by ContextStateManager")
