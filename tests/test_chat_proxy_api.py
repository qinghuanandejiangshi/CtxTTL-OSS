import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

from ctxttl.api import create_app
from ctxttl.application import ContextStateManager
from ctxttl.application.ports import BufferedProviderResponse, StreamingProviderResponse
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
from ctxttl.providers import UpstreamConnectionError
from ctxttl.storage import SQLiteContextStateStore


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, Any], dict[str, str]]] = []
        self.response = BufferedProviderResponse(
            status_code=200,
            headers={"content-type": "application/json", "x-request-id": "upstream-1"},
            body=b'{"id":"chatcmpl_test","choices":[]}',
        )
        self.stream_closed = False
        self.stream_chunks = [b'data: {"id":"chunk-1"}\n\n', b"data: [DONE]\n\n"]

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
            headers={"content-type": "text/event-stream", "x-request-id": "stream-1"},
            body=chunks(),
            close_callback=close,
        )


class FailingTransport(FakeTransport):
    async def complete(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse:
        raise UpstreamConnectionError("unable to reach the upstream provider")


def settings(**overrides: object) -> Settings:
    return Settings(database_url="sqlite:///:memory:", _env_file=None, **overrides)


@asynccontextmanager
async def client_for(
    transport: FakeTransport,
    runtime_settings: Settings | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(runtime_settings or settings(), transport)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client,
    ):
        yield client


async def test_health_does_not_call_upstream() -> None:
    transport = FakeTransport()

    async with client_for(transport) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.2.1"}
    assert transport.requests == []


async def test_openclaw_compile_route_is_a_compile_only_agent_boundary() -> None:
    transport = FakeTransport()
    body = {
        "agent_id": "main",
        "session_key": "weixin/conversation-1",
        "channel_id": "openclaw-weixin",
        "account_id": "account-1",
        "sender_id": "sender-1",
        "task_key": "rag-evaluation",
        "turn_key": "turn-1",
        "request_key": "message-1",
        "payload": {
            "model": "test-model",
            "messages": [{"role": "user", "content": "只编译，不调用模型"}],
        },
    }

    async with client_for(transport) as client:
        response = await client.post("/v1/integrations/openclaw/compile", json=body)

    assert response.status_code == 200
    result = response.json()
    assert result["payload"]["messages"][-1]["content"] == "只编译，不调用模型"
    assert result["trace_id"].startswith("trc_")
    assert result["metrics"]["source_message_tokens"] > 0
    assert result["metrics"]["compiled_message_tokens"] > 0
    assert transport.requests == []


async def test_generic_compile_route_uses_header_identity_without_calling_upstream() -> None:
    transport = FakeTransport()
    payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Compile this request only"}],
    }
    headers = {
        "X-CtxTTL-Session-ID": "session-compile-1",
        "X-CtxTTL-Task-ID": "task-compile-1",
        "X-CtxTTL-Agent-ID": "researcher",
        "X-CtxTTL-Project-ID": "project-1",
        "X-CtxTTL-Turn-ID": "turn-compile-1",
        "X-CtxTTL-Request-ID": "request-compile-1",
    }

    async with client_for(transport) as client:
        response = await client.post("/v1/context/compile", headers=headers, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["messages"][-1]["content"] == "Compile this request only"
    assert body["trace_id"].startswith("trc_")
    assert body["request_id"] == "request-compile-1"
    assert body["metrics"]["source_message_tokens"] > 0
    assert response.headers["x-ctxttl-trace-id"] == body["trace_id"]
    assert response.headers["x-ctxttl-request-id"] == "request-compile-1"
    assert transport.requests == []


async def test_generic_compile_route_requires_session_identity() -> None:
    transport = FakeTransport()

    async with client_for(transport) as client:
        response = await client.post(
            "/v1/context/compile",
            json={"model": "test-model", "messages": []},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_identity"
    assert transport.requests == []


async def test_universal_http_contract_isolates_agent_state_and_shares_task_project_state() -> None:
    transport = FakeTransport()
    shared = {
        "X-CtxTTL-User-ID": "user-1",
        "X-CtxTTL-Project-ID": "project-1",
        "X-CtxTTL-Task-ID": "task-1",
    }
    researcher = {
        **shared,
        "X-CtxTTL-Session-ID": "research-session",
        "X-CtxTTL-Agent-ID": "researcher",
    }
    coder = {
        **shared,
        "X-CtxTTL-Session-ID": "coding-session",
        "X-CtxTTL-Agent-ID": "coder",
    }
    payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Compile the current working state"}],
    }

    async with client_for(transport) as client:
        for headers, body in (
            (
                researcher,
                {
                    "kind": "constraint",
                    "scope": "agent",
                    "applicability": "required",
                    "subject": "research.private-note",
                    "value": "researcher-only",
                },
            ),
            (
                researcher,
                {
                    "kind": "decision",
                    "scope": "task",
                    "applicability": "required",
                    "subject": "task.shared-decision",
                    "value": "shared-by-task",
                },
            ),
            (
                researcher,
                {
                    "kind": "constraint",
                    "scope": "project",
                    "applicability": "required",
                    "subject": "project.shared-rule",
                    "value": "shared-by-project",
                },
            ),
        ):
            asserted = await client.post("/v1/context/items", headers=headers, json=body)
            assert asserted.status_code == 201

        research_result = await client.post("/v1/context/compile", headers=researcher, json=payload)
        coding_result = await client.post("/v1/context/compile", headers=coder, json=payload)

    assert research_result.status_code == 200
    assert coding_result.status_code == 200
    research_messages = json.dumps(research_result.json()["payload"]["messages"])
    coding_messages = json.dumps(coding_result.json()["payload"]["messages"])
    assert "researcher-only" in research_messages
    assert "researcher-only" not in coding_messages
    for shared_value in ("shared-by-task", "shared-by-project"):
        assert shared_value in research_messages
        assert shared_value in coding_messages
    assert transport.requests == []


async def test_openclaw_proxy_maps_runtime_coordinates_before_forwarding() -> None:
    transport = FakeTransport()
    payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "通过透明边界调用模型"}],
    }
    headers = {
        "X-OpenClaw-Agent-ID": "main/agent",
        "X-OpenClaw-Session-Key": "conversation/42",
        "X-OpenClaw-Sender-ID": "wx/user:1",
        "X-OpenClaw-Task-Key": "rag/evaluation",
        "X-OpenClaw-Turn-Key": "turn/3",
        "X-OpenClaw-Request-Key": "message/9",
    }

    async with client_for(transport) as client:
        first = await client.post(
            "/v1/integrations/openclaw/chat/completions",
            headers=headers,
            json=payload,
        )
        second = await client.post(
            "/v1/integrations/openclaw/chat/completions",
            headers={**headers, "X-OpenClaw-Request-Key": "message/10"},
            json=payload,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["x-ctxttl-mode"] == "compile"
    assert first.headers["x-ctxttl-trace-id"].startswith("trc_")
    assert float(first.headers["x-ctxttl-compilation-duration-ms"]) >= 0
    assert float(first.headers["x-ctxttl-upstream-response-start-ms"]) >= 0
    assert float(first.headers["x-ctxttl-proxy-response-start-ms"]) >= 0
    assert transport.requests[0][0] == payload
    first_headers = transport.requests[0][1]
    second_headers = transport.requests[1][1]
    assert first_headers["X-CtxTTL-Session-ID"].startswith("openclaw:session:")
    assert first_headers["X-CtxTTL-Session-ID"] == second_headers["X-CtxTTL-Session-ID"]
    assert first_headers["X-CtxTTL-User-ID"].startswith("openclaw:user:")
    assert first_headers["X-CtxTTL-Task-ID"].startswith("openclaw:task:")
    assert first_headers["X-CtxTTL-Agent-ID"].startswith("openclaw:agent:")
    assert first_headers["X-CtxTTL-Turn-ID"].startswith("openclaw:turn:")
    assert first_headers["X-CtxTTL-Request-ID"] != second_headers["X-CtxTTL-Request-ID"]


async def test_openclaw_proxy_rejects_missing_runtime_identity_locally() -> None:
    transport = FakeTransport()

    async with client_for(transport) as client:
        response = await client.post(
            "/v1/integrations/openclaw/chat/completions",
            json={"model": "test-model", "messages": []},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_identity"
    assert transport.requests == []


async def test_openclaw_proxy_applies_explicit_turn_lifecycle_before_compilation() -> None:
    transport = FakeTransport()
    payload = {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "[[ctxttl-turn:expired-1]] temporary"},
            {"role": "assistant", "content": "temporary answer"},
            {"role": "user", "content": "[[ctxttl-turn:active-2]] current"},
        ],
    }

    async with client_for(transport) as client:
        response = await client.post(
            "/v1/integrations/openclaw/chat/completions",
            headers={
                "X-OpenClaw-Agent-ID": "main",
                "X-OpenClaw-Session-Key": "conversation-42",
                "X-OpenClaw-Inactive-Turn-Keys": "expired=expired-1",
            },
            json=payload,
        )

    assert response.status_code == 200
    assert transport.requests[0][0]["messages"] == [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "[[ctxttl-turn:active-2]] current"},
    ]


async def test_non_streaming_request_is_forwarded_without_schema_loss() -> None:
    transport = FakeTransport()
    payload = {
        "model": "test-model",
        "messages": [
            {"role": "developer", "content": "Keep tool protocol intact."},
            {"role": "assistant", "tool_calls": [{"id": "call-1", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ],
        "future_provider_field": {"enabled": True},
    }

    async with client_for(transport) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json=payload,
        )

    assert response.status_code == 200
    assert response.headers["x-ctxttl-mode"] == "compile"
    assert response.headers["x-ctxttl-trace-id"].startswith("trc_")
    assert response.headers["x-request-id"] == "upstream-1"
    assert transport.requests[0][0] == payload


async def test_missing_session_is_an_openai_shaped_error() -> None:
    transport = FakeTransport()

    async with client_for(transport) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": []},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_identity"
    assert transport.requests == []


async def test_missing_session_can_use_explicit_passthrough_mode() -> None:
    transport = FakeTransport()
    runtime_settings = settings(missing_session_behavior=MissingSessionBehavior.PASSTHROUGH)

    async with client_for(transport, runtime_settings) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": []},
        )

    assert response.status_code == 200
    assert response.headers["x-ctxttl-mode"] == "passthrough"
    assert len(transport.requests) == 1


async def test_invalid_json_and_stream_type_are_rejected_locally() -> None:
    transport = FakeTransport()

    async with client_for(transport) as client:
        invalid_json = await client.post(
            "/v1/chat/completions",
            headers={
                "X-CtxTTL-Session-ID": "session-1",
                "Content-Type": "application/json",
            },
            content=b"not-json",
        )
        invalid_stream = await client.post(
            "/v1/chat/completions",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={"model": "test-model", "messages": [], "stream": "yes"},
        )

    assert invalid_json.status_code == 400
    assert invalid_stream.status_code == 400
    assert transport.requests == []


async def test_invalid_message_protocol_is_rejected_before_upstream() -> None:
    transport = FakeTransport()

    async with client_for(transport) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={
                "model": "test-model",
                "messages": [{"role": "tool", "tool_call_id": "missing-call", "content": "orphan"}],
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_context"
    assert transport.requests == []


async def test_active_context_is_compiled_before_upstream() -> None:
    transport = FakeTransport()
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    owner = ContextOwner(session_id="session-1", user_id="user-1", task_id="task-1")
    await ContextStateManager(store).assert_item(
        ContextItem(
            kind=ContextKind.CONSTRAINT,
            scope=ContextScope.TASK,
            retention=Retention.PERSISTENT,
            authority=Authority.EXPLICIT_USER,
            subject="project.database",
            value="postgresql only",
            owner=owner,
            source=SourceRef(session_id="session-1", turn_id="turn-1"),
        )
    )
    app = create_app(settings(), transport, store, store)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            response = await client.post(
                "/v1/chat/completions",
                headers={
                    "X-CtxTTL-Session-ID": "session-1",
                    "X-CtxTTL-User-ID": "user-1",
                    "X-CtxTTL-Task-ID": "task-1",
                },
                json={"model": "test-model", "messages": [{"role": "user", "content": "go"}]},
            )

        forwarded_messages = transport.requests[0][0]["messages"]
        assert response.status_code == 200
        assert forwarded_messages[-1] == {"role": "user", "content": "go"}
        assert forwarded_messages[0]["role"] == "system"
        assert "postgresql only" in forwarded_messages[0]["content"]
        trace_id = response.headers["x-ctxttl-trace-id"]
        assert await store.get_trace(trace_id) is not None
    finally:
        await store.aclose()


async def test_upstream_error_status_and_body_are_preserved() -> None:
    transport = FakeTransport()
    error_body = {"error": {"message": "rate limited", "type": "rate_limit_error"}}
    transport.response = BufferedProviderResponse(
        status_code=429,
        headers={
            "content-type": "application/json",
            "retry-after": "2",
            "set-cookie": "must-not-be-forwarded=true",
        },
        body=json.dumps(error_body).encode(),
    )

    async with client_for(transport) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={"model": "test-model", "messages": []},
        )

    assert response.status_code == 429
    assert response.json() == error_body
    assert response.headers["retry-after"] == "2"
    assert "set-cookie" not in response.headers


async def test_connection_failure_is_mapped_to_502() -> None:
    async with client_for(FailingTransport()) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={"model": "test-model", "messages": []},
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"


async def test_sse_is_relayed_and_closed() -> None:
    transport = FakeTransport()

    async with (
        client_for(transport) as client,
        client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={"model": "test-model", "messages": [], "stream": True},
        ) as response,
    ):
        body = await response.aread()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-ctxttl-mode"] == "compile"
    assert response.headers["x-ctxttl-trace-id"].startswith("trc_")
    assert body == b'data: {"id":"chunk-1"}\n\ndata: [DONE]\n\n'
    assert transport.stream_closed is True


async def test_default_runtime_archives_and_retrieves_prior_request_messages() -> None:
    transport = FakeTransport()

    async with client_for(transport) as client:
        first = await client.post(
            "/v1/chat/completions",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "The deployment database is PostgreSQL."}],
            },
        )
        second = await client.post(
            "/v1/chat/completions",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "What database did I mention before?"}],
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    forwarded = transport.requests[1][0]["messages"]
    assert forwarded[0]["role"] == "system"
    assert '"authority":"retrieved_source"' in forwarded[0]["content"]
    assert "PostgreSQL" in forwarded[0]["content"]


async def test_openclaw_complete_transcript_does_not_duplicate_archive_history() -> None:
    transport = FakeTransport()
    headers = {
        "X-OpenClaw-Agent-ID": "main",
        "X-OpenClaw-Session-Key": "complete-transcript",
    }

    async with client_for(transport) as client:
        first = await client.post(
            "/v1/integrations/openclaw/chat/completions",
            headers=headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "The database is PostgreSQL."}],
            },
        )
        second_payload = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "What database did I mention?"}],
        }
        second = await client.post(
            "/v1/integrations/openclaw/chat/completions",
            headers=headers,
            json=second_payload,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert transport.requests[1][0] == second_payload


async def test_buffered_assistant_output_is_archived_with_request_id() -> None:
    transport = FakeTransport()
    transport.response = BufferedProviderResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body=json.dumps(
            {"choices": [{"index": 0, "message": {"role": "assistant", "content": "Use SQLite."}}]}
        ).encode(),
    )
    store = SQLiteContextStateStore("sqlite:///:memory:")
    app = create_app(settings(), transport, store, store, store)
    await store.initialize()
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            response = await client.post(
                "/v1/chat/completions",
                headers={
                    "X-CtxTTL-Session-ID": "session-output",
                    "X-CtxTTL-Request-ID": "request-output",
                },
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "Choose a local database."}],
                },
            )
            execution_response = await client.get(
                f"/v1/traces/{response.headers['x-ctxttl-trace-id']}/execution",
                headers={"X-CtxTTL-Session-ID": "session-output"},
            )

        owner = ContextOwner(session_id="session-output")
        hits = await store.search("SQLite", (owner.key_for(ContextScope.SESSION),))
        assert response.status_code == 200
        assert response.headers["x-ctxttl-request-id"] == "request-output"
        assert len(hits) == 1
        assert hits[0].entry.direction.value == "output"
        assert hits[0].entry.request_id == "request-output"
        trace = await store.get_trace(response.headers["x-ctxttl-trace-id"])
        assert trace is not None and trace.request_id == "request-output"
        execution = await store.get_execution(trace.id)
        assert execution is not None
        assert execution.outcome.value == "completed"
        assert execution.streaming is False
        assert execution.status_code == 200
        assert execution.upstream_call_duration_ms is not None
        assert execution.proxy_total_duration_ms >= execution.compilation_duration_ms
        assert execution_response.status_code == 200
        assert execution_response.json()["execution"]["trace_id"] == trace.id
    finally:
        await store.aclose()


async def test_completed_sse_output_is_archived_without_changing_stream_bytes() -> None:
    transport = FakeTransport()
    transport.stream_chunks = [
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"content":"Remember cobalt."}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    expected = b"".join(transport.stream_chunks)
    store = SQLiteContextStateStore("sqlite:///:memory:")
    app = create_app(settings(), transport, store, store, store)
    await store.initialize()
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
            client.stream(
                "POST",
                "/v1/chat/completions",
                headers={
                    "X-CtxTTL-Session-ID": "session-stream-output",
                    "X-CtxTTL-Request-ID": "request-stream-output",
                },
                json={"model": "test-model", "messages": [], "stream": True},
            ) as response,
        ):
            body = await response.aread()

        owner = ContextOwner(session_id="session-stream-output")
        hits = await store.search("cobalt", (owner.key_for(ContextScope.SESSION),))
        assert body == expected
        assert len(hits) == 1
        assert hits[0].entry.message == {
            "role": "assistant",
            "content": "Remember cobalt.",
        }
        assert hits[0].entry.direction.value == "output"
        execution = await store.get_execution(response.headers["x-ctxttl-trace-id"])
        assert execution is not None
        assert execution.outcome.value == "completed"
        assert execution.streaming is True
        assert execution.stream_duration_ms is not None
    finally:
        await store.aclose()


async def test_request_id_replay_is_idempotent_but_conflicting_content_is_rejected() -> None:
    transport = FakeTransport()
    headers = {
        "X-CtxTTL-Session-ID": "session-idempotent",
        "X-CtxTTL-Request-ID": "stable-request",
    }
    payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "first payload marker"}],
    }

    async with client_for(transport) as client:
        first = await client.post("/v1/chat/completions", headers=headers, json=payload)
        replay = await client.post("/v1/chat/completions", headers=headers, json=payload)
        conflict = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "different payload marker"}],
            },
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "request_conflict"
    assert len(transport.requests) == 2
