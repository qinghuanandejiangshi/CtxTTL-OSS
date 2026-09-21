import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from ctxttl.application import ContextStateManager
from ctxttl.application.context_repository import ContextConflict, EventConflict
from ctxttl.models import (
    Authority,
    ContextItem,
    ContextKind,
    ContextOwner,
    ContextScope,
    ContextStatus,
    Retention,
    SourceRef,
)
from ctxttl.storage import SQLiteContextStateStore
from ctxttl.storage.sqlite.store import sqlite_path


def database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def owner(
    session_id: str = "session-1",
    *,
    user_id: str = "user-1",
    task_id: str = "task-1",
) -> ContextOwner:
    return ContextOwner(session_id=session_id, user_id=user_id, task_id=task_id)


def item(
    value: str,
    *,
    context_owner: ContextOwner | None = None,
    subject: str = "project.database",
) -> ContextItem:
    item_owner = context_owner or owner()
    return ContextItem(
        kind=ContextKind.FACT,
        scope=ContextScope.TASK,
        retention=Retention.PERSISTENT,
        authority=Authority.EXPLICIT_USER,
        owner=item_owner,
        source=SourceRef(session_id=item_owner.session_id, turn_id="turn-1"),
        subject=subject,
        value=value,
    )


@asynccontextmanager
async def initialized_store(path: Path) -> AsyncIterator[SQLiteContextStateStore]:
    store = SQLiteContextStateStore(database_url(path))
    await store.initialize()
    try:
        yield store
    finally:
        await store.aclose()


def test_sqlite_url_requires_explicit_scheme() -> None:
    assert sqlite_path("sqlite:///:memory:") == ":memory:"
    with pytest.raises(ValueError, match="sqlite"):
        sqlite_path("postgresql://localhost/ctxttl")


async def test_assert_persists_event_item_and_active_projection(tmp_path: Path) -> None:
    async with initialized_store(tmp_path / "state.db") as store:
        manager = ContextStateManager(store)

        result = await manager.assert_item(item("postgresql"), reason="user selected database")

        assert result.applied is True
        assert result.item is not None
        assert result.item.source_event_id == result.event.id
        assert await store.get_item(result.item.id) == result.item
        assert await store.list_active([result.item.owner_key]) == [result.item]
        assert await store.list_events("session-1") == [result.event]


async def test_database_survives_store_reopen(tmp_path: Path) -> None:
    path = tmp_path / "persistent.db"
    async with initialized_store(path) as first_store:
        result = await ContextStateManager(first_store).assert_item(item("postgresql"))
        assert result.item is not None

    async with initialized_store(path) as second_store:
        assert await second_store.get_item(result.item.id) == result.item
        assert len(await second_store.list_events()) == 1


async def test_supersede_is_an_atomic_state_transition(tmp_path: Path) -> None:
    async with initialized_store(tmp_path / "supersede.db") as store:
        manager = ContextStateManager(store)
        original = await manager.assert_item(item("mysql"))
        assert original.item is not None

        replacement_owner = owner(session_id="session-2")
        replacement = item("postgresql", context_owner=replacement_owner)
        corrected = await manager.supersede(
            original.item.id,
            replacement,
            reason="explicit user correction",
        )

        stored_original = await store.get_item(original.item.id)
        assert stored_original is not None
        assert stored_original.status == ContextStatus.SUPERSEDED
        assert corrected.item is not None
        assert corrected.item.supersedes == original.item.id
        assert await store.list_active([replacement.owner_key]) == [corrected.item]
        assert len(await store.list_events()) == 2


async def test_retract_removes_item_from_active_projection(tmp_path: Path) -> None:
    async with initialized_store(tmp_path / "retract.db") as store:
        manager = ContextStateManager(store)
        asserted = await manager.assert_item(item("postgresql"))
        assert asserted.item is not None

        await manager.retract(
            asserted.item.id,
            owner=owner(session_id="session-2"),
            source=SourceRef(session_id="session-2", turn_id="turn-8"),
            authority=Authority.EXPLICIT_USER,
            reason="user withdrew the preference",
        )

        stored = await store.get_item(asserted.item.id)
        assert stored is not None
        assert stored.status == ContextStatus.RETRACTED
        assert await store.list_active([asserted.item.owner_key]) == []


async def test_expire_removes_item_from_active_projection(tmp_path: Path) -> None:
    async with initialized_store(tmp_path / "expire.db") as store:
        manager = ContextStateManager(store)
        asserted = await manager.assert_item(item("temporary-value"))
        assert asserted.item is not None

        result = await manager.expire(
            asserted.item.id,
            owner=owner(session_id="session-2"),
            source=SourceRef(session_id="session-2", turn_id="turn-9"),
            authority=Authority.SYSTEM,
            reason="lease elapsed",
        )

        stored = await store.get_item(asserted.item.id)
        assert result.item is None
        assert stored is not None
        assert stored.status == ContextStatus.EXPIRED
        assert await store.list_active([asserted.item.owner_key]) == []


async def test_active_items_are_isolated_by_owner_key(tmp_path: Path) -> None:
    async with initialized_store(tmp_path / "isolation.db") as store:
        manager = ContextStateManager(store)
        first = await manager.assert_item(item("postgresql", context_owner=owner(user_id="user-1")))
        second = await manager.assert_item(item("mysql", context_owner=owner(user_id="user-2")))
        assert first.item is not None and second.item is not None

        assert await store.list_active([first.item.owner_key]) == [first.item]
        assert await store.list_active([second.item.owner_key]) == [second.item]


async def test_duplicate_event_retry_is_idempotent_after_later_transition(tmp_path: Path) -> None:
    async with initialized_store(tmp_path / "idempotency.db") as store:
        manager = ContextStateManager(store)
        asserted = await manager.assert_item(item("mysql"))
        assert asserted.item is not None
        await manager.supersede(asserted.item.id, item("postgresql"))

        retried = await store.apply(asserted.event, asserted.item)

        assert retried.applied is False
        assert retried.item is not None
        assert retried.item.status == ContextStatus.SUPERSEDED
        assert len(await store.list_events()) == 2


async def test_reused_event_id_with_different_content_is_rejected(tmp_path: Path) -> None:
    async with initialized_store(tmp_path / "event-conflict.db") as store:
        asserted = await ContextStateManager(store).assert_item(item("postgresql"))
        assert asserted.item is not None
        conflicting_event = asserted.event.model_copy(update={"value": "mysql"})

        with pytest.raises(EventConflict):
            await store.apply(conflicting_event, asserted.item)

        assert len(await store.list_events()) == 1


async def test_failed_replacement_does_not_append_an_event(tmp_path: Path) -> None:
    async with initialized_store(tmp_path / "rollback.db") as store:
        manager = ContextStateManager(store)
        asserted = await manager.assert_item(item("mysql"))
        assert asserted.item is not None

        with pytest.raises(ContextConflict, match="scope and subject"):
            await manager.supersede(
                asserted.item.id,
                item("postgresql", subject="project.cache"),
            )

        stored = await store.get_item(asserted.item.id)
        assert stored is not None
        assert stored.status == ContextStatus.ACTIVE
        assert len(await store.list_events()) == 1


async def test_same_active_subject_requires_explicit_supersede(tmp_path: Path) -> None:
    async with initialized_store(tmp_path / "unique-state.db") as store:
        manager = ContextStateManager(store)
        first = await manager.assert_item(item("mysql"))
        assert first.item is not None

        with pytest.raises(ContextConflict, match="uniqueness"):
            await manager.assert_item(item("postgresql"))

        assert await store.list_active([first.item.owner_key]) == [first.item]
        assert len(await store.list_events()) == 1


async def test_schema_v5_backfills_existing_turn_leases(tmp_path: Path) -> None:
    path = tmp_path / "turn-lease-migration.db"
    lease_owner = ContextOwner(session_id="session-1")
    leased = ContextItem(
        kind=ContextKind.CONSTRAINT,
        scope=ContextScope.SESSION,
        retention=Retention.LEASED,
        authority=Authority.EXPLICIT_USER,
        subject="temporary.constraint",
        value="brief",
        owner=lease_owner,
        source=SourceRef(session_id="session-1", turn_id="turn-0"),
        ttl_turns=1,
    )
    async with initialized_store(path) as store:
        asserted = await ContextStateManager(store).assert_item(leased)
        assert asserted.item is not None

    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE context_turn_leases")
        connection.execute("DROP TABLE turn_observations")
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    finally:
        connection.close()

    async with initialized_store(path) as migrated:
        first = await migrated.observe_turn(
            [leased.owner_key],
            session_id="session-1",
            turn_id="turn-1",
        )
        exhausted = await migrated.observe_turn(
            [leased.owner_key],
            session_id="session-1",
            turn_id="turn-2",
        )

    assert first == []
    assert exhausted == [leased.id]
