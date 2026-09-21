import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx

from ctxttl.api import create_app
from ctxttl.application import (
    ChatCompletionProxy,
    ChatContextCompilationService,
    ContextStateManager,
    ResponsesContextCompilationService,
)
from ctxttl.application.ports import BufferedProviderResponse, StreamingProviderResponse
from ctxttl.application.provider_capture import (
    responses_assistant_messages,
    responses_usage,
    streaming_responses_assistant_messages,
    streaming_responses_terminal,
    streaming_responses_usage,
)
from ctxttl.compiler import OpenAIContextCompiler
from ctxttl.config import MissingSessionBehavior, Settings
from ctxttl.models import (
    Authority,
    ContextItem,
    ContextKind,
    ContextOwner,
    ContextScope,
    Retention,
    SourceRef,
)
from ctxttl.storage import SQLiteContextStateStore


class ResponsesTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, Any], dict[str, str]]] = []
        self.model_requests: list[tuple[list[tuple[str, str]], dict[str, str]]] = []
        self.response = BufferedProviderResponse(
            status_code=200,
            headers={"content-type": "application/json", "x-request-id": "upstream-response-1"},
            body=json.dumps(
                {
                    "id": "resp_1",
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Use PostgreSQL."}],
                        }
                    ],
                    "usage": {
                        "input_tokens": 70,
                        "input_tokens_details": {"cached_tokens": 40},
                        "output_tokens": 8,
                    },
                }
            ).encode(),
        )
        self.stream_chunks: list[bytes] = []
        self.stream_closed = False

    async def list_models(
        self,
        query_params: Sequence[tuple[str, str]],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse:
        self.model_requests.append((list(query_params), dict(request_headers)))
        return BufferedProviderResponse(
            status_code=200,
            headers={"content-type": "application/json", "x-request-id": "models-1"},
            body=b'{"object":"list","data":[]}',
        )

    async def complete(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse:
        self.requests.append((dict(payload), dict(request_headers)))
        return self.response

    async def stream(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> StreamingProviderResponse:
        self.requests.append((dict(payload), dict(request_headers)))

        async def chunks() -> AsyncIterator[bytes]:
            for chunk in self.stream_chunks:
                yield chunk

        async def close() -> None:
            self.stream_closed = True

        return StreamingProviderResponse(
            status_code=200,
            headers={"content-type": "text/event-stream", "x-request-id": "stream-response-1"},
            body=chunks(),
            close_callback=close,
        )


def settings(**overrides: object) -> Settings:
    return Settings(database_url="sqlite:///:memory:", _env_file=None, **overrides)


@asynccontextmanager
async def client_for(
    transport: ResponsesTransport,
    *,
    runtime_settings: Settings | None = None,
    store: SQLiteContextStateStore | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    runtime_store = store or SQLiteContextStateStore("sqlite:///:memory:")
    owns_store = store is None
    app = create_app(
        runtime_settings or settings(),
        transport,
        runtime_store,
        runtime_store,
        runtime_store,
        responses_transport=transport,
        model_catalog_transport=transport,
    )
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            yield client
    finally:
        if owns_store:
            await runtime_store.aclose()


async def test_model_catalog_is_relayed_without_context_compilation() -> None:
    transport = ResponsesTransport()

    async with client_for(transport) as client:
        response = await client.get(
            "/v1/models?client_version=0.155.0&feature=responses",
            headers={"Authorization": "Bearer catalog-key"},
        )

    assert response.status_code == 200
    assert response.content == b'{"object":"list","data":[]}'
    assert response.headers["x-request-id"] == "models-1"
    query, headers = transport.model_requests[0]
    assert query == [("client_version", "0.155.0"), ("feature", "responses")]
    assert headers["authorization"] == "Bearer catalog-key"
    assert transport.requests == []


async def test_responses_proxy_compiles_explicit_input_and_records_provider_usage() -> None:
    transport = ResponsesTransport()
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    owner = ContextOwner(session_id="session-1", task_id="task-1")
    await ContextStateManager(store).assert_item(
        ContextItem(
            kind=ContextKind.CONSTRAINT,
            scope=ContextScope.TASK,
            retention=Retention.PERSISTENT,
            authority=Authority.EXPLICIT_USER,
            subject="project.database",
            value="PostgreSQL only",
            owner=owner,
            source=SourceRef(session_id="session-1", turn_id="turn-1"),
        )
    )
    payload = {
        "model": "test-model",
        "instructions": "Implement the requested change.",
        "input": [
            {"type": "reasoning", "encrypted_content": "opaque-state"},
            {"role": "user", "content": "Which database should be used?"},
        ],
        "tools": [{"type": "function", "name": "save", "parameters": {}}],
        "store": False,
    }
    try:
        async with client_for(transport, store=store) as client:
            response = await client.post(
                "/v1/responses",
                headers={
                    "X-CtxTTL-Session-ID": "session-1",
                    "X-CtxTTL-Task-ID": "task-1",
                    "X-CtxTTL-Request-ID": "responses-request-1",
                },
                json=payload,
            )

        forwarded = transport.requests[0][0]
        assert response.status_code == 200
        assert response.content == transport.response.body
        assert response.headers["x-ctxttl-mode"] == "compile"
        assert response.headers["x-request-id"] == "upstream-response-1"
        assert forwarded["instructions"] == payload["instructions"]
        assert forwarded["tools"] == payload["tools"]
        assert forwarded["store"] is False
        assert forwarded["input"][0] == payload["input"][0]
        assert forwarded["input"][-1] == payload["input"][-1]
        context_messages = [
            item
            for item in forwarded["input"]
            if item.get("role") == "system" and "PostgreSQL only" in item.get("content", "")
        ]
        assert len(context_messages) == 1
        assert all("_ctxttl_responses_source" not in item for item in forwarded["input"])

        execution = await store.get_execution(response.headers["x-ctxttl-trace-id"])
        assert execution is not None
        assert execution.protocol == "responses"
        assert execution.provider_input_tokens == 70
        assert execution.provider_cached_input_tokens == 40
        assert execution.provider_output_tokens == 8
    finally:
        await store.aclose()


async def test_responses_compile_mode_rejects_hidden_upstream_history() -> None:
    transport = ResponsesTransport()

    async with client_for(transport) as client:
        response = await client.post(
            "/v1/responses",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={
                "model": "test-model",
                "previous_response_id": "resp_hidden",
                "input": "continue",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_context"
    assert "upstream-hidden" in response.json()["error"]["message"]
    assert transport.requests == []


async def test_responses_passthrough_mode_preserves_stateful_request() -> None:
    transport = ResponsesTransport()
    payload = {
        "model": "test-model",
        "previous_response_id": "resp_hidden",
        "input": "continue",
    }

    async with client_for(
        transport,
        runtime_settings=settings(missing_session_behavior=MissingSessionBehavior.PASSTHROUGH),
    ) as client:
        response = await client.post("/v1/responses", json=payload)

    assert response.status_code == 200
    assert response.headers["x-ctxttl-mode"] == "passthrough"
    assert transport.requests[0][0] == payload
    assert "x-ctxttl-trace-id" not in response.headers


async def test_responses_sse_is_relayed_closed_and_captured() -> None:
    transport = ResponsesTransport()
    completed = {
        "type": "response.completed",
        "response": {
            "id": "resp_stream",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Remember cobalt."}],
                }
            ],
            "usage": {
                "input_tokens": 30,
                "input_tokens_details": {"cached_tokens": 10},
                "output_tokens": 4,
            },
        },
    }
    transport.stream_chunks = [
        b'event: response.created\ndata: {"type":"response.created"}\n\n',
        f"event: response.completed\ndata: {json.dumps(completed)}\n\n".encode(),
    ]
    expected = b"".join(transport.stream_chunks)
    store = SQLiteContextStateStore("sqlite:///:memory:")
    try:
        async with (
            client_for(transport, store=store) as client,
            client.stream(
                "POST",
                "/v1/responses",
                headers={"X-CtxTTL-Session-ID": "session-stream"},
                json={"model": "test-model", "input": "hello", "stream": True},
            ) as response,
        ):
            body = await response.aread()

        assert body == expected
        assert transport.stream_closed is True
        execution = await store.get_execution(response.headers["x-ctxttl-trace-id"])
        assert execution is not None
        assert execution.protocol == "responses"
        assert execution.streaming is True
        assert execution.provider_input_tokens == 30
        assert execution.provider_cached_input_tokens == 10
        assert execution.provider_output_tokens == 4
        owner = ContextOwner(session_id="session-stream")
        hits = await store.search("cobalt", (owner.key_for(ContextScope.SESSION),))
        assert len(hits) == 1
        assert hits[0].entry.direction.value == "output"
    finally:
        await store.aclose()


async def test_responses_sse_archives_output_item_when_terminal_snapshot_is_empty() -> None:
    transport = ResponsesTransport()
    message = {
        "id": "msg_item_done",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Remember amber."}],
    }
    output_item = {"type": "response.output_item.done", "item": message}
    completed = {
        "type": "response.completed",
        "response": {
            "id": "resp_stream",
            "status": "completed",
            "output": [],
            "usage": {
                "input_tokens": 24,
                "input_tokens_details": {"cached_tokens": 8},
                "output_tokens": 3,
            },
        },
    }
    transport.stream_chunks = [
        f"event: response.output_item.done\ndata: {json.dumps(output_item)}\n\n".encode(),
        f"event: response.completed\ndata: {json.dumps(completed)}\n\n".encode(),
    ]
    store = SQLiteContextStateStore("sqlite:///:memory:")
    try:
        async with (
            client_for(transport, store=store) as client,
            client.stream(
                "POST",
                "/v1/responses",
                headers={"X-CtxTTL-Session-ID": "session-output-item"},
                json={"model": "test-model", "input": "hello", "stream": True},
            ) as response,
        ):
            await response.aread()

        owner = ContextOwner(session_id="session-output-item")
        hits = await store.search("amber", (owner.key_for(ContextScope.SESSION),))
        assert len(hits) == 1
        assert hits[0].entry.direction.value == "output"
    finally:
        await store.aclose()


async def test_terminal_event_records_execution_before_stream_eof() -> None:
    transport = ResponsesTransport()
    release = asyncio.Event()
    completed = {
        "type": "response.completed",
        "response": {
            "id": "resp_terminal",
            "status": "completed",
            "output": [],
            "usage": {
                "input_tokens": 21,
                "input_tokens_details": {"cached_tokens": 13},
                "output_tokens": 2,
            },
        },
    }

    async def terminal_then_wait() -> AsyncIterator[bytes]:
        yield f"data: {json.dumps(completed)}\n\n".encode()
        await release.wait()

    async def stream(
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> StreamingProviderResponse:
        return StreamingProviderResponse(
            status_code=200,
            headers={"content-type": "text/event-stream"},
            body=terminal_then_wait(),
            close_callback=lambda: _set_event(release),
        )

    transport.stream = stream  # type: ignore[method-assign]
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    runtime_settings = settings()
    compilation = ChatContextCompilationService(
        store,
        OpenAIContextCompiler(),
        target_tokens=runtime_settings.target_context_tokens,
        max_tokens=runtime_settings.max_context_tokens,
        recent_turn_reserve=runtime_settings.recent_turn_reserve,
        trace_store=store,
    )
    proxy = ChatCompletionProxy(
        transport,
        runtime_settings,
        ResponsesContextCompilationService(compilation),
        store,
        buffered_output_parser=responses_assistant_messages,
        streaming_output_parser=streaming_responses_assistant_messages,
        buffered_usage_parser=responses_usage,
        streaming_usage_parser=streaming_responses_usage,
        streaming_terminal_detector=streaming_responses_terminal,
        protocol="responses",
    )
    prepared = proxy.prepare(
        {"model": "test", "input": "hello", "stream": True},
        {"X-CtxTTL-Session-ID": "terminal-session"},
    )

    try:
        response = await proxy.stream(prepared)
        await anext(response.body)
        execution = await store.get_execution(response.trace_id or "")

        assert execution is not None
        assert execution.outcome.value == "completed"
        assert execution.provider_input_tokens == 21
        assert execution.provider_cached_input_tokens == 13
        assert execution.provider_output_tokens == 2
    finally:
        release.set()
        await response.body.aclose()  # type: ignore[attr-defined]
        await response.aclose()
        await store.aclose()


async def _set_event(event: asyncio.Event) -> None:
    event.set()
