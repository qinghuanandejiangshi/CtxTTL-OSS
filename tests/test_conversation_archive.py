"""Conversation archive, isolation, FTS, and retention tests."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from ctxttl.archive import ArchiveConflict, ConversationEntry, MessageDirection
from ctxttl.models import ContextOwner
from ctxttl.storage.sqlite import SQLiteContextStateStore
from ctxttl.storage.sqlite.schema import MIGRATION_1, MIGRATION_2, MIGRATION_3


def make_entry(
    *,
    session_id: str = "session-a",
    user_id: str = "user-a",
    text: str = "The deployment database is PostgreSQL.",
    trace_id: str = "trace-a",
    request_id: str = "request-a",
    direction: MessageDirection = MessageDirection.INPUT,
    position: int = 0,
    created_at: datetime | None = None,
) -> ConversationEntry:
    return ConversationEntry.create(
        owner=ContextOwner(session_id=session_id, user_id=user_id),
        message={"role": "user", "content": text},
        source_trace_id=trace_id,
        request_id=request_id,
        direction=direction,
        position=position,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_archive_append_is_idempotent_and_searchable(tmp_path) -> None:
    store = SQLiteContextStateStore(f"sqlite:///{tmp_path / 'archive.db'}")
    await store.initialize()
    entry = make_entry()

    first = await store.append([entry])
    duplicate = make_entry(trace_id="trace-retry")
    second = await store.append([duplicate])
    hits = await store.search("deployment PostgreSQL", entry.owner_keys, limit=5)

    assert first == [entry]
    assert second == [entry]
    assert [hit.entry for hit in hits] == [entry]
    await store.aclose()


@pytest.mark.asyncio
async def test_identical_messages_in_distinct_requests_remain_distinct_occurrences(
    tmp_path,
) -> None:
    store = SQLiteContextStateStore(f"sqlite:///{tmp_path / 'occurrences.db'}")
    await store.initialize()
    first = make_entry(request_id="request-1")
    second = make_entry(request_id="request-2")

    await store.append([first, second])
    hits = await store.search("PostgreSQL", first.owner_keys)

    assert {hit.entry.id for hit in hits} == {first.id, second.id}
    await store.aclose()


@pytest.mark.asyncio
async def test_reused_occurrence_with_different_content_is_rejected(tmp_path) -> None:
    store = SQLiteContextStateStore(f"sqlite:///{tmp_path / 'conflict.db'}")
    await store.initialize()
    first = make_entry(request_id="request-1", text="PostgreSQL")
    conflicting = make_entry(request_id="request-1", text="MySQL")
    await store.append([first])

    with pytest.raises(ArchiveConflict):
        await store.append([conflicting])

    await store.aclose()


@pytest.mark.asyncio
async def test_input_and_output_positions_have_separate_idempotency_keys(tmp_path) -> None:
    store = SQLiteContextStateStore(f"sqlite:///{tmp_path / 'directions.db'}")
    await store.initialize()
    input_entry = make_entry(request_id="request-1")
    output_entry = make_entry(
        request_id="request-1",
        direction=MessageDirection.OUTPUT,
        text="The answer is PostgreSQL.",
    )

    await store.append([input_entry, output_entry])
    hits = await store.search("PostgreSQL", input_entry.owner_keys)

    assert {hit.entry.direction for hit in hits} == {
        MessageDirection.INPUT,
        MessageDirection.OUTPUT,
    }
    await store.aclose()


@pytest.mark.asyncio
async def test_archive_search_enforces_owner_boundaries(tmp_path) -> None:
    store = SQLiteContextStateStore(f"sqlite:///{tmp_path / 'isolation.db'}")
    await store.initialize()
    alice = make_entry(session_id="alice-session", user_id="alice")
    bob = make_entry(session_id="bob-session", user_id="bob", trace_id="trace-b")
    await store.append([alice, bob])

    alice_hits = await store.search("PostgreSQL", alice.owner_keys)
    unknown_hits = await store.search("PostgreSQL", ['["session","unknown"]'])

    assert [hit.entry.owner.user_id for hit in alice_hits] == ["alice"]
    assert unknown_hits == []
    await store.aclose()


@pytest.mark.asyncio
async def test_archive_supports_chinese_and_ignores_empty_queries(tmp_path) -> None:
    store = SQLiteContextStateStore(f"sqlite:///{tmp_path / 'chinese.db'}")
    await store.initialize()
    entry = make_entry(text="项目部署在腾讯云，数据库使用 PostgreSQL。")
    await store.append([entry])

    assert len(await store.search("之前说的腾讯云部署是什么", entry.owner_keys)) == 1
    assert await store.search("!!!", entry.owner_keys) == []
    assert await store.search("", entry.owner_keys) == []
    await store.aclose()


@pytest.mark.asyncio
async def test_archive_prune_removes_entries_and_fts_rows(tmp_path) -> None:
    store = SQLiteContextStateStore(f"sqlite:///{tmp_path / 'prune.db'}")
    await store.initialize()
    now = datetime.now(UTC)
    old = make_entry(
        text="legacy database choice",
        request_id="request-old",
        created_at=now - timedelta(days=31),
    )
    current = make_entry(
        text="current database choice",
        request_id="request-current",
        created_at=now,
    )
    await store.append([old, current])

    deleted = await store.prune(before=now - timedelta(days=30))

    assert deleted == 1
    assert await store.search("legacy", old.owner_keys) == []
    assert len(await store.search("current", current.owner_keys)) == 1
    await store.aclose()


def test_archive_entry_rejects_tampered_content_hash() -> None:
    entry = make_entry()

    with pytest.raises(ValueError, match="content_hash"):
        ConversationEntry.model_validate({**entry.model_dump(), "content_hash": "0" * 64})


@pytest.mark.asyncio
async def test_schema_v3_archive_is_preserved_by_occurrence_migration(tmp_path) -> None:
    path = tmp_path / "migration.db"
    owner = ContextOwner(session_id="legacy-session", user_id="legacy-user")
    message = {"role": "user", "content": "legacy PostgreSQL decision"}
    canonical = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()
    identity = f"{owner.session_id}\0{content_hash}".encode()
    legacy = ConversationEntry.model_validate(
        {
            "id": f"arc_{hashlib.sha256(identity).hexdigest()[:32]}",
            "owner": owner,
            "message": message,
            "content_hash": content_hash,
            "source_trace_id": "trc_legacy",
            "created_at": datetime.now(UTC),
        }
    )
    connection = await aiosqlite.connect(path)
    await connection.executescript(MIGRATION_1)
    await connection.executescript(MIGRATION_2)
    await connection.executescript(MIGRATION_3)
    await connection.execute(
        """
        INSERT INTO conversation_entries (
            id, source_session_id, content_hash, source_trace_id,
            searchable_text, created_at, entry_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            legacy.id,
            legacy.owner.session_id,
            legacy.content_hash,
            legacy.source_trace_id,
            legacy.searchable_text,
            legacy.created_at.isoformat(),
            legacy.model_dump_json(exclude={"request_id", "direction", "position"}),
        ),
    )
    await connection.execute(
        "INSERT INTO conversation_entry_owners (entry_id, owner_key) VALUES (?, ?)",
        (legacy.id, legacy.owner_keys[0]),
    )
    await connection.execute("PRAGMA user_version = 3")
    await connection.commit()
    await connection.close()

    store = SQLiteContextStateStore(f"sqlite:///{path}")
    await store.initialize()
    hits = await store.search("PostgreSQL", legacy.owner_keys)

    assert [hit.entry.id for hit in hits] == [legacy.id]
    assert hits[0].entry.request_id is None
    await store.aclose()
