from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctxttl.application import (
    ChatContextCompilationService,
    ContextStateManager,
    ConversationMemoryService,
)
from ctxttl.archive import ConversationEntry, MessageDirection
from ctxttl.compiler import OpenAIContextCompiler, ProtocolViolation, TokenBudgetExceeded
from ctxttl.identity import RequestIdentity, reachable_owner_keys
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


def item(
    context_id: str,
    scope: ContextScope,
    value: str,
    *,
    session_id: str = "session-1",
    user_id: str = "user-1",
    task_id: str = "task-1",
    agent_id: str | None = None,
    project_id: str | None = None,
) -> ContextItem:
    owner = ContextOwner(
        session_id=session_id,
        user_id=user_id,
        task_id=task_id,
        agent_id=agent_id,
        project_id=project_id,
    )
    return ContextItem(
        id=context_id,
        kind=ContextKind.FACT,
        scope=scope,
        retention=Retention.PERSISTENT,
        authority=Authority.EXPLICIT_USER,
        subject=f"preference.{context_id}",
        value=value,
        owner=owner,
        source=SourceRef(session_id=session_id, turn_id="turn-1"),
    )


async def test_service_loads_only_reachable_ownership_boundaries() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        manager = ContextStateManager(store)
        reachable = [
            item("ctx_session", ContextScope.SESSION, "session"),
            item("ctx_task", ContextScope.TASK, "task"),
            item("ctx_user", ContextScope.USER, "user"),
        ]
        isolated = item(
            "ctx_other_user",
            ContextScope.USER,
            "private",
            session_id="session-2",
            user_id="user-2",
        )
        for context in [*reachable, isolated]:
            await manager.assert_item(context)

        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=2_000,
            max_tokens=4_000,
            recent_turn_reserve=2,
        )
        payload: dict[str, Any] = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "continue"}],
            "future_provider_field": {"enabled": True},
        }
        compiled = await service.compile(
            payload,
            RequestIdentity(session_id="session-1", user_id="user-1", task_id="task-1"),
        )

        assert set(compiled.compilation.selected_context_ids) == {
            context.id for context in reachable
        }
        assert isolated.id not in compiled.compilation.selected_context_ids
        assert compiled.payload["future_provider_field"] == {"enabled": True}
        assert payload["messages"] == [{"role": "user", "content": "continue"}]
    finally:
        await store.aclose()


async def test_multi_agent_compilation_shares_task_and_project_but_not_agent_context() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        manager = ContextStateManager(store)
        researcher_private = item(
            "ctx_researcher_private",
            ContextScope.AGENT,
            "private researcher scratchpad",
            session_id="session-researcher",
            agent_id="researcher",
            project_id="project-1",
        )
        shared_task = item(
            "ctx_shared_task",
            ContextScope.TASK,
            "shared verified task fact",
            session_id="session-researcher",
            agent_id="researcher",
            project_id="project-1",
        )
        shared_project = item(
            "ctx_shared_project",
            ContextScope.PROJECT,
            "shared project constraint",
            session_id="session-researcher",
            agent_id="researcher",
            project_id="project-1",
        )
        for context in (researcher_private, shared_task, shared_project):
            await manager.assert_item(context)

        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=2_000,
            max_tokens=4_000,
            recent_turn_reserve=2,
        )
        payload = {"messages": [{"role": "user", "content": "continue"}]}
        researcher = await service.compile(
            payload,
            RequestIdentity(
                session_id="session-researcher",
                user_id="user-1",
                task_id="task-1",
                agent_id="researcher",
                project_id="project-1",
            ),
        )
        coder = await service.compile(
            payload,
            RequestIdentity(
                session_id="session-coder",
                user_id="user-1",
                task_id="task-1",
                agent_id="coder",
                project_id="project-1",
            ),
        )

        assert set(researcher.compilation.selected_context_ids) == {
            researcher_private.id,
            shared_task.id,
            shared_project.id,
        }
        assert set(coder.compilation.selected_context_ids) == {
            shared_task.id,
            shared_project.id,
        }
        assert coder.trace.agent_id == "coder"
        assert coder.trace.project_id == "project-1"
    finally:
        await store.aclose()


async def test_service_only_loads_turn_context_for_the_matching_turn() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        turn_item = item("ctx_turn", ContextScope.TURN, "only now")
        await ContextStateManager(store).assert_item(turn_item)
        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=2_000,
            max_tokens=4_000,
            recent_turn_reserve=1,
        )
        payload = {"messages": [{"role": "user", "content": "continue"}]}

        matching = await service.compile(
            payload,
            RequestIdentity(session_id="session-1", turn_id="turn-1"),
        )
        other = await service.compile(
            payload,
            RequestIdentity(session_id="session-1", turn_id="turn-2"),
        )

        assert turn_item.id in matching.compilation.selected_context_ids
        assert turn_item.id not in other.compilation.selected_context_ids
        assert matching.trace.turn_id == "turn-1"
    finally:
        await store.aclose()


async def test_turn_lease_counts_distinct_turns_and_expires_before_the_next_use() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        owner = ContextOwner(session_id="session-1")
        leased = ContextItem(
            id="ctx_leased",
            kind=ContextKind.CONSTRAINT,
            scope=ContextScope.SESSION,
            retention=Retention.LEASED,
            authority=Authority.EXPLICIT_USER,
            subject="temporary.constraint",
            value="use twice",
            owner=owner,
            source=SourceRef(session_id="session-1", turn_id="turn-0"),
            ttl_turns=2,
        )
        await ContextStateManager(store).assert_item(leased)
        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=2_000,
            max_tokens=4_000,
            recent_turn_reserve=1,
        )
        payload = {"messages": [{"role": "user", "content": "continue"}]}

        without_turn = await service.compile(payload, RequestIdentity(session_id="session-1"))
        first = await service.compile(
            payload,
            RequestIdentity(session_id="session-1", turn_id="turn-1"),
        )
        retry = await service.compile(
            payload,
            RequestIdentity(session_id="session-1", turn_id="turn-1"),
        )
        second = await service.compile(
            payload,
            RequestIdentity(session_id="session-1", turn_id="turn-2"),
        )
        exhausted = await service.compile(
            payload,
            RequestIdentity(session_id="session-1", turn_id="turn-3"),
        )
        stored = await store.get_item(leased.id)

        assert leased.id not in without_turn.compilation.selected_context_ids
        assert leased.id in first.compilation.selected_context_ids
        assert leased.id in retry.compilation.selected_context_ids
        assert leased.id in second.compilation.selected_context_ids
        assert leased.id not in exhausted.compilation.selected_context_ids
        assert stored is not None and stored.status == ContextStatus.EXPIRED
        events = await store.list_events()
        assert events[-1].event_type.value == "expire"
        assert events[-1].reason == "ttl_turns exhausted"
    finally:
        await store.aclose()


async def test_service_reserves_requested_output_tokens() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=80,
            max_tokens=100,
            recent_turn_reserve=1,
        )
        compiled = await service.compile(
            {
                "messages": [{"role": "user", "content": "short"}],
                "max_completion_tokens": 40,
            },
            RequestIdentity(session_id="session-1"),
        )

        assert compiled.compilation.target_tokens == 60
        assert compiled.compilation.max_tokens == 60

        responses_compiled = await service.compile(
            {
                "messages": [{"role": "user", "content": "short"}],
                "max_output_tokens": 30,
            },
            RequestIdentity(session_id="session-1"),
        )
        assert responses_compiled.compilation.max_tokens == 70

        with pytest.raises(TokenBudgetExceeded, match="no room"):
            await service.compile(
                {"messages": [], "max_tokens": 100},
                RequestIdentity(session_id="session-1"),
            )
        with pytest.raises(ProtocolViolation, match="non-negative integer"):
            await service.compile(
                {"messages": [], "max_tokens": True},
                RequestIdentity(session_id="session-1"),
            )
    finally:
        await store.aclose()


async def test_service_persists_metadata_only_trace_by_default() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=1_000,
            max_tokens=2_000,
            recent_turn_reserve=1,
            trace_store=store,
        )
        compiled = await service.compile(
            {"model": "test-model", "messages": [{"role": "user", "content": "hello"}]},
            RequestIdentity(session_id="session-1"),
        )

        persisted = await store.get_trace(compiled.trace.id)
        assert persisted == compiled.trace
        assert persisted is not None
        assert persisted.source_messages is None
        assert persisted.candidate_context is None
        assert persisted.request_fingerprint != ""
        assert persisted.compilation_duration_ms >= 0
        assert persisted.compilation_timing is not None
        assert persisted.compilation_timing.compiler_ms >= 0
        assert persisted.token_ledger is not None
        assert persisted.token_ledger.provider_input_tokens == persisted.compiled_message_tokens
        assert persisted.token_ledger.removed_before_provider_tokens == 0
    finally:
        await store.aclose()


async def test_full_trace_capture_is_explicit() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        context = item("ctx_trace", ContextScope.SESSION, "remembered")
        await ContextStateManager(store).assert_item(context)
        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=1_000,
            max_tokens=2_000,
            recent_turn_reserve=1,
            trace_capture_content=True,
        )
        source = [{"role": "user", "content": "hello"}]
        compiled = await service.compile(
            {"messages": source},
            RequestIdentity(session_id="session-1", user_id="user-1", task_id="task-1"),
        )

        assert compiled.trace.source_messages == tuple(source)
        assert compiled.trace.candidate_context is not None
        assert compiled.trace.candidate_context[0]["id"] == context.id
    finally:
        await store.aclose()


async def test_service_archives_then_retrieves_history_as_low_authority_context() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=1_000,
            max_tokens=2_000,
            recent_turn_reserve=1,
            conversation_memory=ConversationMemoryService(
                store,
                retrieval_limit=1,
                reference_retrieval_limit=4,
            ),
        )
        identity = RequestIdentity(session_id="session-1", user_id="user-1")
        first = await service.compile(
            {"messages": [{"role": "user", "content": "The deployment database is PostgreSQL."}]},
            identity,
        )
        archived = await store.search("PostgreSQL", reachable_owner_keys(identity))

        second = await service.compile(
            {"messages": [{"role": "user", "content": "What database did I mention before?"}]},
            identity,
        )

        assert first.compilation.selected_context_ids == ()
        assert len(archived) == 1
        assert second.compilation.selected_context_ids == (f"ctx_{archived[0].entry.id}",)
        rendered = second.payload["messages"][0]
        assert rendered["role"] == "system"
        assert '"authority":"retrieved_source"' in rendered["content"]
        assert '"instruction_policy":"evidence_only"' in rendered["content"]
        assert "UNTRUSTED EVIDENCE" in rendered["content"]
        assert "PostgreSQL" in rendered["content"]
        assert second.trace.candidate_context_count == 1
        assert second.trace.retrieved_context_count == 1
        assert (
            second.trace.selected_retrieved_context_ids == second.compilation.selected_context_ids
        )
        assert second.trace.retrieval_ranks == {f"ctx_{archived[0].entry.id}": archived[0].rank}
    finally:
        await store.aclose()


async def test_retrieval_does_not_duplicate_messages_already_in_current_request() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=1_000,
            max_tokens=2_000,
            recent_turn_reserve=2,
            conversation_memory=ConversationMemoryService(store),
        )
        identity = RequestIdentity(session_id="session-1")
        old_message = {"role": "user", "content": "The database is PostgreSQL."}
        await service.compile({"messages": [old_message]}, identity)

        compiled = await service.compile(
            {
                "messages": [
                    old_message,
                    {"role": "assistant", "content": "Understood."},
                    {"role": "user", "content": "What database did I mention before?"},
                ]
            },
            identity,
        )

        assert compiled.compilation.selected_context_ids == ()
        assert compiled.payload["messages"] == [
            old_message,
            {"role": "assistant", "content": "Understood."},
            {"role": "user", "content": "What database did I mention before?"},
        ]
    finally:
        await store.aclose()


async def test_archive_and_retrieval_can_be_disabled_independently() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        identity = RequestIdentity(session_id="session-1")
        owner = ContextOwner(session_id="session-1")
        archived = ConversationEntry.create(
            owner=owner,
            message={"role": "user", "content": "The database is PostgreSQL."},
            source_trace_id="trc_seed",
            request_id="req_seed",
            direction=MessageDirection.INPUT,
        )
        await store.append([archived])
        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=1_000,
            max_tokens=2_000,
            recent_turn_reserve=1,
            conversation_memory=ConversationMemoryService(
                store,
                archive_enabled=False,
                retrieval_enabled=False,
            ),
        )

        compiled = await service.compile(
            {"messages": [{"role": "user", "content": "What database before? uniquemarker"}]},
            identity,
        )

        assert compiled.compilation.selected_context_ids == ()
        assert await store.search("uniquemarker", archived.owner_keys) == []
    finally:
        await store.aclose()


async def test_service_expires_due_context_before_compilation() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        now = datetime.now(UTC)
        due = ContextItem(
            kind=ContextKind.FACT,
            scope=ContextScope.SESSION,
            retention=Retention.LEASED,
            authority=Authority.EXPLICIT_USER,
            subject="temporary.database",
            value="obsolete",
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
            owner=ContextOwner(session_id="session-expiry"),
            source=SourceRef(
                session_id="session-expiry",
                timestamp=now - timedelta(days=2),
            ),
        )
        asserted = await ContextStateManager(store).assert_item(due)
        assert asserted.item is not None
        service = ChatContextCompilationService(
            store,
            OpenAIContextCompiler(),
            target_tokens=1_000,
            max_tokens=2_000,
            recent_turn_reserve=1,
        )

        compiled = await service.compile(
            {"messages": [{"role": "user", "content": "continue"}]},
            RequestIdentity(session_id="session-expiry"),
        )
        stored = await store.get_item(asserted.item.id)

        assert compiled.compilation.selected_context_ids == ()
        assert stored is not None and stored.status.value == "expired"
    finally:
        await store.aclose()
