"""Ownership-safe context queries and lifecycle operations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ctxttl.application.context_repository import (
    ApplyResult,
    ContextConflict,
    ContextNotFound,
    ContextStateStore,
)
from ctxttl.application.context_state import ContextStateManager
from ctxttl.identity import RequestIdentity, reachable_owner_keys
from ctxttl.models import Authority, ContextItem, ContextOwner, ContextStatus, SourceRef
from ctxttl.models.context import utc_now


class LifecycleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str | None = Field(default=None, max_length=1_000)
    turn_id: str | None = Field(default=None, min_length=1, max_length=128)


class ContextManagementService:
    """Expose context state without allowing callers to cross ownership boundaries."""

    def __init__(self, store: ContextStateStore) -> None:
        self._store = store
        self._manager = ContextStateManager(store)

    async def list_items(
        self,
        identity: RequestIdentity,
        *,
        status: ContextStatus | None = None,
        limit: int = 100,
        turn_id: str | None = None,
    ) -> list[ContextItem]:
        effective_turn_id = self._effective_turn_id(identity, turn_id)
        owner_keys = reachable_owner_keys(identity, turn_id=effective_turn_id)
        if status in {None, ContextStatus.ACTIVE}:
            await self._expire_due(identity, owner_keys, turn_id=effective_turn_id)
        return await self._store.list_items(owner_keys, status=status, limit=limit)

    async def list_active(
        self,
        identity: RequestIdentity,
        *,
        turn_id: str | None = None,
    ) -> list[ContextItem]:
        effective_turn_id = self._effective_turn_id(identity, turn_id)
        owner_keys = reachable_owner_keys(identity, turn_id=effective_turn_id)
        await self._expire_due(identity, owner_keys, turn_id=effective_turn_id)
        return await self._store.list_active(owner_keys)

    async def list_for_compilation(self, identity: RequestIdentity) -> list[ContextItem]:
        """Advance an explicit logical turn, then return context safe for compilation."""

        owner_keys = reachable_owner_keys(identity)
        if identity.turn_id is not None:
            exhausted_ids = await self._store.observe_turn(
                owner_keys,
                session_id=identity.session_id,
                turn_id=identity.turn_id,
            )
            for context_id in exhausted_ids:
                item = await self._store.get_item(context_id)
                if item is not None and item.status == ContextStatus.ACTIVE:
                    await self._expire_item(
                        item,
                        identity,
                        turn_id=identity.turn_id,
                        reason="ttl_turns exhausted",
                    )
        await self._expire_due(identity, owner_keys, turn_id=identity.turn_id)
        active = await self._store.list_active(owner_keys)
        if identity.turn_id is None:
            return [item for item in active if item.ttl_turns is None]
        return active

    async def get_item(
        self,
        context_id: str,
        identity: RequestIdentity,
        *,
        turn_id: str | None = None,
    ) -> ContextItem:
        effective_turn_id = self._effective_turn_id(identity, turn_id)
        item = await self._required_reachable(context_id, identity, turn_id=effective_turn_id)
        if item.status == ContextStatus.ACTIVE and self._is_due(item, utc_now()):
            await self._expire_item(
                item,
                identity,
                turn_id=effective_turn_id,
                reason="expires_at elapsed",
            )
            refreshed = await self._store.get_item(context_id)
            assert refreshed is not None
            return refreshed
        return item

    async def retract(
        self,
        context_id: str,
        identity: RequestIdentity,
        request: LifecycleInput,
    ) -> ApplyResult:
        turn_id = self._effective_turn_id(identity, request.turn_id)
        item = await self._required_reachable(context_id, identity, turn_id=turn_id)
        return await self._manager.retract(
            item.id,
            owner=self._owner(identity),
            source=SourceRef(session_id=identity.session_id, turn_id=turn_id),
            authority=Authority.EXPLICIT_USER,
            reason=request.reason,
        )

    async def expire(
        self,
        context_id: str,
        identity: RequestIdentity,
        request: LifecycleInput,
    ) -> ApplyResult:
        turn_id = self._effective_turn_id(identity, request.turn_id)
        item = await self._required_reachable(context_id, identity, turn_id=turn_id)
        return await self._manager.expire(
            item.id,
            owner=self._owner(identity),
            source=SourceRef(session_id=identity.session_id, turn_id=turn_id),
            authority=Authority.EXPLICIT_USER,
            reason=request.reason,
        )

    async def _expire_due(
        self,
        identity: RequestIdentity,
        owner_keys: tuple[str, ...],
        *,
        turn_id: str | None,
    ) -> None:
        now = utc_now()
        active = await self._store.list_active(owner_keys)
        for item in active:
            if self._is_due(item, now):
                await self._expire_item(
                    item,
                    identity,
                    turn_id=turn_id,
                    reason="expires_at elapsed",
                )

    async def _expire_item(
        self,
        item: ContextItem,
        identity: RequestIdentity,
        *,
        turn_id: str | None,
        reason: str,
    ) -> None:
        try:
            await self._manager.expire(
                item.id,
                owner=self._owner(identity),
                source=SourceRef(session_id=identity.session_id, turn_id=turn_id),
                authority=Authority.SYSTEM,
                reason=reason,
            )
        except ContextConflict:
            current = await self._store.get_item(item.id)
            if current is not None and current.status != ContextStatus.ACTIVE:
                return
            raise

    async def _required_reachable(
        self,
        context_id: str,
        identity: RequestIdentity,
        *,
        turn_id: str | None,
    ) -> ContextItem:
        item = await self._store.get_item(context_id)
        if item is None or item.owner_key not in reachable_owner_keys(identity, turn_id=turn_id):
            raise ContextNotFound(f"context item not found: {context_id}")
        return item

    @staticmethod
    def _is_due(item: ContextItem, now: datetime) -> bool:
        return item.expires_at is not None and item.expires_at <= now

    @staticmethod
    def _owner(identity: RequestIdentity) -> ContextOwner:
        return ContextOwner(
            session_id=identity.session_id,
            user_id=identity.user_id,
            task_id=identity.task_id,
            agent_id=identity.agent_id,
            project_id=identity.project_id,
        )

    @staticmethod
    def _effective_turn_id(identity: RequestIdentity, requested_turn_id: str | None) -> str | None:
        if (
            identity.turn_id is not None
            and requested_turn_id is not None
            and identity.turn_id != requested_turn_id
        ):
            raise ContextConflict("body turn_id must match X-CtxTTL-Turn-ID")
        return identity.turn_id or requested_turn_id
