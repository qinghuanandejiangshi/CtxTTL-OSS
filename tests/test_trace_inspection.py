"""Ownership-safe trace query and deterministic replay tests."""

from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from ctxttl.api import create_app
from ctxttl.application import (
    ChatContextCompilationService,
    ContextStateManager,
    ReplayUnavailable,
    TraceInspectionService,
)
from ctxttl.application.ports import BufferedProviderResponse, StreamingProviderResponse
from ctxttl.compiler import OpenAIContextCompiler
from ctxttl.config import Settings
from ctxttl.identity import RequestIdentity
from ctxttl.models import (
    Authority,
    ContextItem,
    ContextKind,
    ContextOwner,
    ContextScope,
    Retention,
    SourceRef,
)
from ctxttl.observability import CompilationTrace
from ctxttl.storage import SQLiteContextStateStore


class UnusedTransport:
    async def complete(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse:
        raise AssertionError("trace inspection must not call the provider")

    async def stream(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> StreamingProviderResponse:
        raise AssertionError("trace inspection must not call the provider")


async def captured_trace(store: SQLiteContextStateStore) -> CompilationTrace:
    owner = ContextOwner(session_id="session-1")
    await ContextStateManager(store).assert_item(
        ContextItem(
            kind=ContextKind.CONSTRAINT,
            scope=ContextScope.SESSION,
            retention=Retention.PERSISTENT,
            authority=Authority.EXPLICIT_USER,
            subject="response.language",
            value="Chinese",
            owner=owner,
            source=SourceRef(session_id="session-1"),
        )
    )
    compilation = ChatContextCompilationService(
        store,
        OpenAIContextCompiler(),
        target_tokens=1_000,
        max_tokens=2_000,
        recent_turn_reserve=2,
        trace_store=store,
        trace_capture_content=True,
    )
    return (
        await compilation.compile(
            {"model": "test-model", "messages": [{"role": "user", "content": "hello"}]},
            RequestIdentity(session_id="session-1", turn_id="turn-1"),
            request_id="request-1",
        )
    ).trace


async def test_captured_trace_replays_to_the_recorded_compilation() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        trace = await captured_trace(store)
        replay = await TraceInspectionService(store, OpenAIContextCompiler()).replay(
            trace.id,
            RequestIdentity(session_id="session-1"),
        )

        assert replay.matches is True
        assert replay.mismatches == ()
        assert replay.selected_context_ids == trace.selected_context_ids
        assert replay.compiled_message_tokens == trace.compiled_message_tokens
    finally:
        await store.aclose()


async def test_trace_api_is_session_isolated_and_reports_replay_requirements() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        trace = await captured_trace(store)
        without_content = trace.model_copy(
            update={
                "id": "trc_without_content",
                "source_messages": None,
                "candidate_context": None,
            }
        )
        await store.record_trace(without_content)
        app = create_app(
            Settings(database_url="sqlite:///:memory:", _env_file=None),
            UnusedTransport(),
            store,
            store,
            store,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            headers = {"X-CtxTTL-Session-ID": "session-1"}
            listed = await client.get("/v1/traces", headers=headers)
            fetched = await client.get(f"/v1/traces/{trace.id}", headers=headers)
            isolated = await client.get(
                f"/v1/traces/{trace.id}",
                headers={"X-CtxTTL-Session-ID": "session-2"},
            )
            unavailable = await client.post(
                f"/v1/traces/{without_content.id}/replay",
                headers=headers,
            )

        assert listed.status_code == 200
        assert {item["id"] for item in listed.json()["traces"]} == {
            trace.id,
            without_content.id,
        }
        assert fetched.status_code == 200
        assert fetched.json()["trace"]["turn_id"] == "turn-1"
        assert isolated.status_code == 404
        assert unavailable.status_code == 409
        assert unavailable.json()["error"]["code"] == "replay_unavailable"
    finally:
        await store.aclose()


async def test_replay_rejects_legacy_trace_without_compiler_revision() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        trace = await captured_trace(store)
        legacy = trace.model_copy(update={"id": "trc_legacy", "compiler_revision": None})
        await store.record_trace(legacy)

        with pytest.raises(ReplayUnavailable, match="revision"):
            await TraceInspectionService(store, OpenAIContextCompiler()).replay(
                legacy.id,
                RequestIdentity(session_id="session-1"),
            )
    finally:
        await store.aclose()
