import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ctxttl.observability import (
    CompilationTrace,
    ExecutionOutcome,
    ExecutionTrace,
    TokenSavingsLedger,
    TraceConflict,
    TraceDecision,
)
from ctxttl.storage import SQLiteContextStateStore
from ctxttl.storage.sqlite.schema import MIGRATION_1


def trace(
    trace_id: str = "trc_test",
    *,
    session_id: str = "session-1",
    created_at: datetime | None = None,
) -> CompilationTrace:
    return CompilationTrace(
        id=trace_id,
        created_at=created_at or datetime(2026, 9, 8, tzinfo=UTC),
        session_id=session_id,
        user_id="user-1",
        task_id="task-1",
        model="test-model",
        streaming=False,
        request_fingerprint="a" * 64,
        source_message_count=2,
        compiled_message_count=2,
        candidate_context_count=1,
        source_message_tokens=20,
        compiled_message_tokens=30,
        output_token_reserve=100,
        target_input_tokens=1_000,
        max_input_tokens=2_000,
        compilation_duration_ms=1.25,
        selected_context_ids=("ctx_1",),
        decisions=(
            TraceDecision(
                candidate_id="ctx_1",
                candidate_type="context",
                included=True,
                reason="soft_selected",
                token_cost=10,
                utility=0.8,
            ),
        ),
    )


def test_trace_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        trace(created_at=datetime(2026, 9, 8))


def test_token_ledger_accounts_for_low_relevance_context() -> None:
    ledger = TokenSavingsLedger(
        source_transcript_tokens=10,
        candidate_context_tokens=12,
        selected_transcript_tokens=10,
        selected_context_tokens=4,
        message_budget_excluded_tokens=0,
        inactive_context_tokens=0,
        redundant_context_tokens=0,
        low_relevance_context_tokens=8,
        context_budget_excluded_tokens=0,
        provider_input_tokens=14,
        removed_before_provider_tokens=8,
    )

    assert ledger.low_relevance_context_tokens == 8


async def test_sqlite_trace_round_trip_is_idempotent_and_isolated() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        first = trace("trc_1")
        newer = trace("trc_2", created_at=first.created_at + timedelta(seconds=1))
        other = trace("trc_other", session_id="session-2")
        await store.record_trace(first)
        await store.record_trace(newer)
        await store.record_trace(other)
        await store.record_trace(first)

        assert await store.get_trace(first.id) == first
        assert await store.list_traces("session-1") == [newer, first]
        assert await store.list_traces("session-2") == [other]
        with pytest.raises(TraceConflict):
            await store.record_trace(first.model_copy(update={"model": "different"}))
    finally:
        await store.aclose()


async def test_sqlite_execution_trace_round_trip_is_idempotent() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        compilation = trace("trc_abc")
        await store.record_trace(compilation)
        execution = ExecutionTrace(
            trace_id=compilation.id,
            session_id=compilation.session_id,
            request_id="request-1",
            streaming=True,
            outcome=ExecutionOutcome.COMPLETED,
            status_code=200,
            started_at=compilation.created_at,
            completed_at=compilation.created_at + timedelta(seconds=1),
            compilation_duration_ms=1.5,
            upstream_call_duration_ms=20,
            stream_duration_ms=900,
            proxy_total_duration_ms=921.5,
        )

        await store.record_execution(execution)
        await store.record_execution(execution)

        assert await store.get_execution(compilation.id) == execution
        with pytest.raises(TraceConflict):
            await store.record_execution(
                execution.model_copy(update={"proxy_total_duration_ms": 999})
            )
    finally:
        await store.aclose()


async def test_schema_version_one_database_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "migrate.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(MIGRATION_1)
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()

    store = SQLiteContextStateStore(f"sqlite:///{path}")
    await store.initialize()
    try:
        persisted = trace()
        await store.record_trace(persisted)
        assert await store.get_trace(persisted.id) == persisted
    finally:
        await store.aclose()
