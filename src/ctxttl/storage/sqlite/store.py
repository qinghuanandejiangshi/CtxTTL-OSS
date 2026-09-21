"""Transactional SQLite event store and context state projection."""

import asyncio
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import aiosqlite

from ctxttl.application.context_repository import (
    ApplyResult,
    ContextConflict,
    ContextNotFound,
    EventConflict,
    RequestConflict,
)
from ctxttl.archive import ArchiveConflict, ArchiveSearchHit, ConversationEntry
from ctxttl.models import ContextEvent, ContextItem, ContextStatus, EventType
from ctxttl.models.context import as_utc, utc_now
from ctxttl.observability import CompilationTrace, ExecutionTrace, TraceConflict
from ctxttl.storage.sqlite.schema import (
    MIGRATION_1,
    MIGRATION_2,
    MIGRATION_3,
    MIGRATION_4,
    MIGRATION_5,
    MIGRATION_6,
    MIGRATION_7,
    SCHEMA_VERSION,
)


def sqlite_path(database_url: str) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("MVP storage requires a sqlite:/// database URL")
    path = database_url[len(prefix) :]
    if not path:
        raise ValueError("SQLite database path cannot be empty")
    return path


class SQLiteContextStateStore:
    """SQLite implementation of the atomic context state store contract."""

    def __init__(self, database_url: str) -> None:
        self._path = sqlite_path(database_url)
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            if self._path != ":memory:":
                Path(self._path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self._path)
            try:
                connection.row_factory = aiosqlite.Row
                await connection.execute("PRAGMA foreign_keys = ON")
                if self._path != ":memory:":
                    await connection.execute("PRAGMA journal_mode = WAL")
                version_row = await (await connection.execute("PRAGMA user_version")).fetchone()
                version = int(version_row[0])
                if version > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"database schema {version} is newer than supported {SCHEMA_VERSION}"
                    )
                if version < 1:
                    await connection.executescript(MIGRATION_1)
                    await connection.execute("PRAGMA user_version = 1")
                    version = 1
                if version < 2:
                    await connection.executescript(MIGRATION_2)
                    await connection.execute("PRAGMA user_version = 2")
                    version = 2
                if version < 3:
                    await connection.executescript(MIGRATION_3)
                    await connection.execute("PRAGMA user_version = 3")
                    version = 3
                if version < 4:
                    await connection.executescript(MIGRATION_4)
                    await connection.execute("PRAGMA user_version = 4")
                    version = 4
                if version < 5:
                    await connection.executescript(MIGRATION_5)
                    await connection.execute("PRAGMA user_version = 5")
                    version = 5
                if version < 6:
                    await connection.executescript(MIGRATION_6)
                    await connection.execute("PRAGMA user_version = 6")
                    version = 6
                if version < 7:
                    await connection.executescript(MIGRATION_7)
                    await connection.execute("PRAGMA user_version = 7")
                await connection.commit()
            except Exception:
                await connection.close()
                raise
            self._connection = connection

    async def apply(
        self,
        event: ContextEvent,
        item: ContextItem | None = None,
    ) -> ApplyResult:
        async with self._lock:
            connection = self._required_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                result = await self._apply_in_transaction(connection, event, item)
                await connection.commit()
            except aiosqlite.IntegrityError as error:
                await connection.rollback()
                raise ContextConflict("context state violates a uniqueness constraint") from error
            except Exception:
                await connection.rollback()
                raise
        return result

    async def apply_idempotent(
        self,
        event: ContextEvent,
        item: ContextItem | None,
        *,
        session_id: str,
        request_id: str,
        request_fingerprint: str,
        operation: str,
    ) -> ApplyResult:
        async with self._lock:
            connection = self._required_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._idempotent_result(
                    connection,
                    session_id=session_id,
                    request_id=request_id,
                    operation=operation,
                    request_fingerprint=request_fingerprint,
                )
                if existing is not None:
                    await connection.commit()
                    return existing
                result = await self._apply_in_transaction(connection, event, item)
                await connection.execute(
                    """
                    INSERT INTO mutation_requests (
                        source_session_id, request_id, operation, request_fingerprint,
                        event_json, item_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        request_id,
                        operation,
                        request_fingerprint,
                        result.event.model_dump_json(),
                        result.item.model_dump_json() if result.item is not None else None,
                        utc_now().isoformat(),
                    ),
                )
                await connection.commit()
            except aiosqlite.IntegrityError as error:
                await connection.rollback()
                raise ContextConflict("context state violates a uniqueness constraint") from error
            except Exception:
                await connection.rollback()
                raise
        return result

    async def get_item(self, context_id: str) -> ContextItem | None:
        async with self._lock:
            connection = self._required_connection()
            row = await (
                await connection.execute(
                    "SELECT item_json FROM context_items WHERE id = ?",
                    (context_id,),
                )
            ).fetchone()
        return ContextItem.model_validate_json(row["item_json"]) if row else None

    async def list_active(self, owner_keys: Sequence[str]) -> list[ContextItem]:
        if not owner_keys:
            return []
        placeholders = ",".join("?" for _ in owner_keys)
        query = f"""
            SELECT item_json
            FROM context_items
            WHERE status = ? AND owner_key IN ({placeholders})
            ORDER BY created_at, id
        """
        parameters = (ContextStatus.ACTIVE.value, *owner_keys)
        async with self._lock:
            connection = self._required_connection()
            rows = await (await connection.execute(query, parameters)).fetchall()
        return [ContextItem.model_validate_json(row["item_json"]) for row in rows]

    async def list_items(
        self,
        owner_keys: Sequence[str],
        *,
        status: ContextStatus | None = None,
        limit: int = 100,
    ) -> list[ContextItem]:
        if not owner_keys:
            return []
        if not 1 <= limit <= 1_000:
            raise ValueError("context item limit must be between 1 and 1000")
        placeholders = ",".join("?" for _ in owner_keys)
        status_clause = " AND status = ?" if status is not None else ""
        query = f"""
            SELECT item_json
            FROM context_items
            WHERE owner_key IN ({placeholders}){status_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """
        parameters: tuple[object, ...] = (*owner_keys,)
        if status is not None:
            parameters += (status.value,)
        parameters += (limit,)
        async with self._lock:
            connection = self._required_connection()
            rows = await (await connection.execute(query, parameters)).fetchall()
        return [ContextItem.model_validate_json(row["item_json"]) for row in rows]

    async def observe_turn(
        self,
        owner_keys: Sequence[str],
        *,
        session_id: str,
        turn_id: str,
    ) -> list[str]:
        """Record one logical turn per owner and return leases that are now exhausted."""

        if not owner_keys:
            return []
        observed_at = utc_now().isoformat()
        async with self._lock:
            connection = self._required_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                for owner_key in dict.fromkeys(owner_keys):
                    cursor = await connection.execute(
                        """
                        INSERT OR IGNORE INTO turn_observations (
                            owner_key, source_session_id, turn_id, observed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (owner_key, session_id, turn_id, observed_at),
                    )
                    if cursor.rowcount == 1:
                        await connection.execute(
                            """
                            UPDATE context_turn_leases
                            SET consumed_turns = consumed_turns + 1
                            WHERE owner_key = ?
                            """,
                            (owner_key,),
                        )
                placeholders = ",".join("?" for _ in owner_keys)
                rows = await (
                    await connection.execute(
                        f"""
                        SELECT leases.context_item_id
                        FROM context_turn_leases AS leases
                        JOIN context_items AS items ON items.id = leases.context_item_id
                        WHERE leases.owner_key IN ({placeholders})
                          AND leases.consumed_turns > leases.ttl_turns
                          AND items.status = ?
                        ORDER BY leases.context_item_id
                        """,
                        (*owner_keys, ContextStatus.ACTIVE.value),
                    )
                ).fetchall()
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return [str(row["context_item_id"]) for row in rows]

    async def list_events(self, session_id: str | None = None) -> list[ContextEvent]:
        if session_id is None:
            query = "SELECT event_json FROM events ORDER BY rowid"
            parameters: tuple[str, ...] = ()
        else:
            query = """
                SELECT event_json FROM events
                WHERE source_session_id = ?
                ORDER BY rowid
            """
            parameters = (session_id,)
        async with self._lock:
            connection = self._required_connection()
            rows = await (await connection.execute(query, parameters)).fetchall()
        return [ContextEvent.model_validate_json(row["event_json"]) for row in rows]

    async def record_trace(self, trace: CompilationTrace) -> None:
        async with self._lock:
            connection = self._required_connection()
            row = await (
                await connection.execute(
                    "SELECT trace_json FROM compilation_traces WHERE id = ?",
                    (trace.id,),
                )
            ).fetchone()
            if row is not None:
                existing = CompilationTrace.model_validate_json(row["trace_json"])
                if existing != trace:
                    raise TraceConflict("trace ID was already used with different content")
                return
            try:
                await connection.execute(
                    """
                    INSERT INTO compilation_traces (
                        id, source_session_id, request_fingerprint, created_at, trace_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        trace.id,
                        trace.session_id,
                        trace.request_fingerprint,
                        trace.created_at.isoformat(),
                        trace.model_dump_json(),
                    ),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def get_trace(self, trace_id: str) -> CompilationTrace | None:
        async with self._lock:
            connection = self._required_connection()
            row = await (
                await connection.execute(
                    "SELECT trace_json FROM compilation_traces WHERE id = ?",
                    (trace_id,),
                )
            ).fetchone()
        return CompilationTrace.model_validate_json(row["trace_json"]) if row else None

    async def record_execution(self, execution: ExecutionTrace) -> None:
        async with self._lock:
            connection = self._required_connection()
            row = await (
                await connection.execute(
                    "SELECT execution_json FROM execution_traces WHERE trace_id = ?",
                    (execution.trace_id,),
                )
            ).fetchone()
            if row is not None:
                existing = ExecutionTrace.model_validate_json(row["execution_json"])
                if existing != execution:
                    raise TraceConflict(
                        "execution trace ID was already used with different content"
                    )
                return
            try:
                await connection.execute(
                    """
                    INSERT INTO execution_traces (
                        trace_id, source_session_id, completed_at, execution_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        execution.trace_id,
                        execution.session_id,
                        execution.completed_at.isoformat(),
                        execution.model_dump_json(),
                    ),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def get_execution(self, trace_id: str) -> ExecutionTrace | None:
        async with self._lock:
            connection = self._required_connection()
            row = await (
                await connection.execute(
                    "SELECT execution_json FROM execution_traces WHERE trace_id = ?",
                    (trace_id,),
                )
            ).fetchone()
        return ExecutionTrace.model_validate_json(row["execution_json"]) if row else None

    async def list_traces(
        self,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[CompilationTrace]:
        if not 1 <= limit <= 1_000:
            raise ValueError("trace limit must be between 1 and 1000")
        async with self._lock:
            connection = self._required_connection()
            rows = await (
                await connection.execute(
                    """
                    SELECT trace_json
                    FROM compilation_traces
                    WHERE source_session_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (session_id, limit),
                )
            ).fetchall()
        return [CompilationTrace.model_validate_json(row["trace_json"]) for row in rows]

    async def append(
        self,
        entries: Sequence[ConversationEntry],
    ) -> list[ConversationEntry]:
        """Atomically append request-relative occurrences and return persisted forms."""

        if not entries:
            return []
        async with self._lock:
            connection = self._required_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                stored: list[ConversationEntry] = []
                for entry in entries:
                    await connection.execute(
                        """
                        INSERT OR IGNORE INTO conversation_entries (
                            id, source_session_id, request_id, direction, position,
                            content_hash, source_trace_id, searchable_text, created_at, entry_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry.id,
                            entry.owner.session_id,
                            entry.request_id or entry.source_trace_id,
                            entry.direction.value,
                            entry.position,
                            entry.content_hash,
                            entry.source_trace_id,
                            entry.searchable_text,
                            entry.created_at.isoformat(),
                            entry.model_dump_json(),
                        ),
                    )
                    row = await (
                        await connection.execute(
                            "SELECT entry_json FROM conversation_entries WHERE id = ?",
                            (entry.id,),
                        )
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("archive insert did not produce a readable entry")
                    persisted = ConversationEntry.model_validate_json(row["entry_json"])
                    if not self._same_occurrence(persisted, entry):
                        raise ArchiveConflict(
                            "archive request occurrence was already used with different content"
                        )
                    if persisted.id == entry.id and persisted.created_at == entry.created_at:
                        for owner_key in entry.owner_keys:
                            await connection.execute(
                                """
                                INSERT OR IGNORE INTO conversation_entry_owners (
                                    entry_id, owner_key
                                )
                                VALUES (?, ?)
                                """,
                                (entry.id, owner_key),
                            )
                    stored.append(persisted)
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return stored

    @staticmethod
    def _same_occurrence(first: ConversationEntry, second: ConversationEntry) -> bool:
        return (
            first.id == second.id
            and first.owner == second.owner
            and first.message == second.message
            and first.content_hash == second.content_hash
            and first.request_id == second.request_id
            and first.direction == second.direction
            and first.position == second.position
        )

    async def search(
        self,
        query: str,
        owner_keys: Sequence[str],
        *,
        limit: int = 10,
    ) -> list[ArchiveSearchHit]:
        if not owner_keys or not query.strip():
            return []
        if not 1 <= limit <= 100:
            raise ValueError("archive search limit must be between 1 and 100")
        match_query = self._fts_match_query(query)
        if match_query is None:
            return []
        placeholders = ",".join("?" for _ in owner_keys)
        sql = f"""
            SELECT e.entry_json, bm25(conversation_entries_fts) AS rank
            FROM conversation_entries_fts
            JOIN conversation_entries AS e ON e.rowid = conversation_entries_fts.rowid
            WHERE conversation_entries_fts MATCH ?
              AND EXISTS (
                  SELECT 1 FROM conversation_entry_owners AS owners
                  WHERE owners.entry_id = e.id
                    AND owners.owner_key IN ({placeholders})
              )
            ORDER BY rank, e.created_at DESC, e.id
            LIMIT ?
        """
        parameters = (match_query, *owner_keys, limit)
        async with self._lock:
            connection = self._required_connection()
            rows = await (await connection.execute(sql, parameters)).fetchall()
        return [
            ArchiveSearchHit(
                entry=ConversationEntry.model_validate_json(row["entry_json"]),
                rank=float(row["rank"]),
            )
            for row in rows
        ]

    async def prune(self, *, before: datetime) -> int:
        normalized_before = as_utc(before)
        assert normalized_before is not None
        normalized = normalized_before.isoformat()
        async with self._lock:
            connection = self._required_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute(
                    "DELETE FROM conversation_entries WHERE created_at < ?",
                    (normalized,),
                )
                deleted = cursor.rowcount
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return deleted

    @staticmethod
    def _fts_match_query(query: str) -> str | None:
        raw_terms = re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]+", query)
        terms: list[str] = []
        for term in raw_terms:
            if re.fullmatch(r"[\u3400-\u9fff]+", term) and len(term) > 3:
                terms.extend(term[index : index + 3] for index in range(len(term) - 2))
            else:
                terms.append(term)
            if len(terms) >= 16:
                break
        terms = terms[:16]
        if not terms:
            return None
        return " OR ".join(f'"{term}"' for term in terms)

    async def aclose(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    def _required_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("context store must be initialized before use")
        return self._connection

    async def _apply_in_transaction(
        self,
        connection: aiosqlite.Connection,
        event: ContextEvent,
        item: ContextItem | None,
    ) -> ApplyResult:
        duplicate = await self._duplicate_result(connection, event, item)
        if duplicate is not None:
            return duplicate
        self._validate_event_item(event, item)
        if event.event_type == EventType.ASSERT:
            assert item is not None
            await self._insert_event(connection, event, item.id)
            await self._insert_item(connection, item)
            await self._insert_projection(connection, item)
            await self._insert_turn_lease(connection, item)
        elif event.event_type == EventType.SUPERSEDE:
            assert item is not None
            target = await self._required_active_target(connection, event)
            self._validate_replacement(target, item)
            await self._insert_event(connection, event, item.id)
            await self._replace_status(connection, target, ContextStatus.SUPERSEDED)
            await self._remove_projection(connection, target.id)
            await self._insert_item(connection, item)
            await self._insert_projection(connection, item)
            await self._insert_turn_lease(connection, item)
        else:
            target = await self._required_active_target(connection, event)
            status = (
                ContextStatus.RETRACTED
                if event.event_type == EventType.RETRACT
                else ContextStatus.EXPIRED
            )
            await self._insert_event(connection, event, None)
            await self._replace_status(connection, target, status)
            await self._remove_projection(connection, target.id)
        return ApplyResult(event=event, item=item, applied=True)

    @staticmethod
    async def _idempotent_result(
        connection: aiosqlite.Connection,
        *,
        session_id: str,
        request_id: str,
        operation: str,
        request_fingerprint: str,
    ) -> ApplyResult | None:
        row = await (
            await connection.execute(
                """
                SELECT request_fingerprint, event_json, item_json
                FROM mutation_requests
                WHERE source_session_id = ? AND request_id = ? AND operation = ?
                """,
                (session_id, request_id, operation),
            )
        ).fetchone()
        if row is None:
            return None
        if row["request_fingerprint"] != request_fingerprint:
            raise RequestConflict("request ID was already used with different context input")
        return ApplyResult(
            event=ContextEvent.model_validate_json(row["event_json"]),
            item=(
                ContextItem.model_validate_json(row["item_json"])
                if row["item_json"] is not None
                else None
            ),
            applied=False,
        )

    async def _duplicate_result(
        self,
        connection: aiosqlite.Connection,
        event: ContextEvent,
        item: ContextItem | None,
    ) -> ApplyResult | None:
        row = await (
            await connection.execute(
                "SELECT event_json, item_id FROM events WHERE id = ?",
                (event.id,),
            )
        ).fetchone()
        if row is None:
            return None
        stored_event = ContextEvent.model_validate_json(row["event_json"])
        incoming_item_id = item.id if item else None
        if stored_event != event or row["item_id"] != incoming_item_id:
            raise EventConflict("event ID was already used with different content")
        stored_item = None
        if row["item_id"] is not None:
            assert item is not None
            item_row = await (
                await connection.execute(
                    "SELECT item_json FROM context_items WHERE id = ?",
                    (row["item_id"],),
                )
            ).fetchone()
            if item_row is None:
                raise ContextNotFound("event references a missing context item")
            stored_item = ContextItem.model_validate_json(item_row["item_json"])
            comparable_item = item.model_copy(update={"status": stored_item.status})
            if comparable_item != stored_item:
                raise EventConflict("event item differs from the already persisted item")
        return ApplyResult(event=stored_event, item=stored_item, applied=False)

    @staticmethod
    def _validate_event_item(event: ContextEvent, item: ContextItem | None) -> None:
        requires_item = event.event_type in {EventType.ASSERT, EventType.SUPERSEDE}
        if requires_item != (item is not None):
            raise ContextConflict(f"{event.event_type.value} item payload is invalid")
        if item is None:
            return
        if item.source_event_id != event.id:
            raise ContextConflict("context item must reference its source event")
        if item.status != ContextStatus.ACTIVE:
            raise ContextConflict("new context item must be active")
        if (
            event.subject != (item.subject or item.id)
            or event.scope != item.scope
            or event.authority != item.authority
            or event.owner_key != item.owner_key
            or event.source != item.source
            or event.value != item.value
        ):
            raise ContextConflict("event and context item describe different assertions")
        if event.event_type == EventType.ASSERT and item.supersedes is not None:
            raise ContextConflict("asserted context cannot supersede another item")
        if event.event_type == EventType.SUPERSEDE and item.supersedes != event.target_context_id:
            raise ContextConflict("replacement must reference the superseded target")

    async def _required_active_target(
        self,
        connection: aiosqlite.Connection,
        event: ContextEvent,
    ) -> ContextItem:
        row = await (
            await connection.execute(
                "SELECT item_json FROM context_items WHERE id = ?",
                (event.target_context_id,),
            )
        ).fetchone()
        if row is None:
            raise ContextNotFound(f"context item not found: {event.target_context_id}")
        target = ContextItem.model_validate_json(row["item_json"])
        if target.status != ContextStatus.ACTIVE:
            raise ContextConflict("target context is not active")
        if (
            event.owner_key != target.owner_key
            or event.scope != target.scope
            or event.subject != (target.subject or target.id)
        ):
            raise ContextConflict("event does not match the target ownership boundary")
        return target

    @staticmethod
    def _validate_replacement(target: ContextItem, item: ContextItem) -> None:
        if (
            target.owner_key != item.owner_key
            or target.scope != item.scope
            or target.subject != item.subject
        ):
            raise ContextConflict("replacement does not match the active assertion")

    @staticmethod
    async def _insert_event(
        connection: aiosqlite.Connection,
        event: ContextEvent,
        item_id: str | None,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO events (
                id, event_type, source_session_id, owner_key, scope, subject,
                target_context_id, item_id, created_at, event_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.event_type.value,
                event.source.session_id,
                event.owner_key,
                event.scope.value,
                event.subject,
                event.target_context_id,
                item_id,
                event.created_at.isoformat(),
                event.model_dump_json(),
            ),
        )

    @staticmethod
    async def _insert_item(
        connection: aiosqlite.Connection,
        item: ContextItem,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO context_items (
                id, source_event_id, owner_key, scope, kind, subject, status,
                supersedes, created_at, item_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.source_event_id,
                item.owner_key,
                item.scope.value,
                item.kind.value,
                item.subject,
                item.status.value,
                item.supersedes,
                item.created_at.isoformat(),
                item.model_dump_json(),
            ),
        )

    @staticmethod
    async def _insert_projection(
        connection: aiosqlite.Connection,
        item: ContextItem,
    ) -> None:
        if item.subject is None:
            return
        await connection.execute(
            """
            INSERT INTO state_assertions (
                context_item_id, owner_key, scope, subject, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (item.id, item.owner_key, item.scope.value, item.subject, item.created_at.isoformat()),
        )

    @staticmethod
    async def _insert_turn_lease(
        connection: aiosqlite.Connection,
        item: ContextItem,
    ) -> None:
        if item.ttl_turns is None:
            return
        await connection.execute(
            """
            INSERT INTO context_turn_leases (
                context_item_id, owner_key, ttl_turns, consumed_turns
            ) VALUES (?, ?, ?, 0)
            """,
            (item.id, item.owner_key, item.ttl_turns),
        )

    @staticmethod
    async def _replace_status(
        connection: aiosqlite.Connection,
        item: ContextItem,
        status: ContextStatus,
    ) -> None:
        updated = item.model_copy(update={"status": status})
        await connection.execute(
            "UPDATE context_items SET status = ?, item_json = ? WHERE id = ?",
            (status.value, updated.model_dump_json(), item.id),
        )

    @staticmethod
    async def _remove_projection(
        connection: aiosqlite.Connection,
        context_id: str,
    ) -> None:
        await connection.execute(
            "DELETE FROM state_assertions WHERE context_item_id = ?",
            (context_id,),
        )
