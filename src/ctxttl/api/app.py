"""FastAPI application factory and Chat Completions protocol translation."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ctxttl import __version__
from ctxttl.application import (
    ChatCompletionProxy,
    ChatContextCompilationService,
    ContextManagementService,
    ConversationMemoryService,
    LifecycleAwareAgentContext,
    LifecycleInput,
    ReplayUnavailable,
    ResponsesContextCompilationService,
    StructuredContextIngestionService,
    StructuredContextInput,
    StructuredIngestionError,
    TraceInspectionService,
    TraceNotFound,
)
from ctxttl.application.context_repository import (
    ContextConflict,
    ContextNotFound,
    ContextStateStore,
    RequestConflict,
)
from ctxttl.application.ports import (
    BufferedProviderResponse,
    ChatCompletionTransport,
    ModelCatalogTransport,
    StreamingProviderResponse,
)
from ctxttl.application.provider_capture import (
    responses_assistant_messages,
    responses_usage,
    streaming_responses_assistant_messages,
    streaming_responses_terminal,
    streaming_responses_usage,
)
from ctxttl.archive import ArchiveConflict, ConversationArchive
from ctxttl.compiler import CompilationError, OpenAIContextCompiler
from ctxttl.config import Settings
from ctxttl.identity import (
    IdentityError,
    MissingSessionIdentity,
    RequestIdentity,
    resolve_identity,
    resolve_request_id,
)
from ctxttl.integrations import OpenClawCompilationInput, OpenClawContextAdapter
from ctxttl.models import ContextStatus
from ctxttl.observability import (
    CompilationTraceStore,
    ExecutionTraceStore,
    PreCompilationExclusion,
)
from ctxttl.providers import (
    HttpxChatCompletionTransport,
    HttpxOpenAITransport,
    HttpxResponsesTransport,
    UpstreamConnectionError,
)
from ctxttl.storage import SQLiteContextStateStore

_RESPONSE_HEADER_NAMES = {"content-type", "retry-after", "x-request-id"}
_RESPONSE_HEADER_PREFIXES = ("openai-", "x-ratelimit-")


def _response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in _RESPONSE_HEADER_NAMES
        or key.lower().startswith(_RESPONSE_HEADER_PREFIXES)
    }


def _timing_headers(
    upstream: BufferedProviderResponse | StreamingProviderResponse,
) -> dict[str, str]:
    values = {
        "X-CtxTTL-Compilation-Duration-Ms": upstream.compilation_duration_ms,
        "X-CtxTTL-Upstream-Response-Start-Ms": upstream.upstream_response_start_ms,
        "X-CtxTTL-Proxy-Response-Start-Ms": upstream.proxy_response_start_ms,
    }
    return {name: f"{value:.3f}" for name, value in values.items() if value is not None}


def _error_response(message: str, *, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "invalid_request_error" if status_code == 400 else "api_error",
                "param": None,
                "code": code,
            }
        },
    )


async def _proxy_response(
    proxy: ChatCompletionProxy,
    payload: Mapping[str, Any],
    request_headers: Mapping[str, str],
    pre_compilation_exclusion: PreCompilationExclusion | None = None,
) -> Response:
    prepared = proxy.prepare(
        payload,
        request_headers,
        pre_compilation_exclusion=pre_compilation_exclusion,
    )
    if payload.get("stream", False):
        upstream = await proxy.stream(prepared)

        async def relay() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.body:
                    yield chunk
            finally:
                try:
                    close_body = getattr(upstream.body, "aclose", None)
                    if callable(close_body):
                        await close_body()
                finally:
                    await upstream.aclose()

        headers = _response_headers(upstream.headers)
        headers.update(_timing_headers(upstream))
        headers["X-CtxTTL-Mode"] = prepared.mode.value
        headers["X-CtxTTL-Request-ID"] = prepared.request_id
        if upstream.trace_id is not None:
            headers["X-CtxTTL-Trace-ID"] = upstream.trace_id
        return StreamingResponse(relay(), status_code=upstream.status_code, headers=headers)

    upstream = await proxy.complete(prepared)
    headers = _response_headers(upstream.headers)
    headers.update(_timing_headers(upstream))
    headers["X-CtxTTL-Mode"] = prepared.mode.value
    headers["X-CtxTTL-Request-ID"] = prepared.request_id
    if upstream.trace_id is not None:
        headers["X-CtxTTL-Trace-ID"] = upstream.trace_id
    return Response(content=upstream.body, status_code=upstream.status_code, headers=headers)


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as error:
        raise InvalidRequestBody("request body must be valid JSON") from error
    if not isinstance(payload, dict):
        raise InvalidRequestBody("request body must be a JSON object")
    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise InvalidRequestBody("stream must be a boolean")
    return payload


class InvalidRequestBody(ValueError):
    """Raised when the API cannot safely interpret the proxy request."""


def create_app(
    settings: Settings | None = None,
    transport: ChatCompletionTransport | None = None,
    state_store: ContextStateStore | None = None,
    trace_store: CompilationTraceStore | None = None,
    archive: ConversationArchive | None = None,
    responses_transport: ChatCompletionTransport | None = None,
    model_catalog_transport: ModelCatalogTransport | None = None,
) -> FastAPI:
    """Build an application with replaceable settings and provider transport."""

    runtime_settings = settings or Settings()
    runtime_transport = transport or HttpxChatCompletionTransport(runtime_settings)
    owns_transport = transport is None
    runtime_responses_transport = responses_transport or HttpxResponsesTransport(runtime_settings)
    owns_responses_transport = responses_transport is None
    runtime_model_catalog_transport = model_catalog_transport
    if runtime_model_catalog_transport is None and isinstance(
        runtime_responses_transport, HttpxOpenAITransport
    ):
        runtime_model_catalog_transport = runtime_responses_transport
    runtime_store = state_store or SQLiteContextStateStore(runtime_settings.database_url)
    owns_store = state_store is None
    runtime_trace_store = trace_store
    if runtime_trace_store is None and state_store is None:
        runtime_trace_store = runtime_store
    runtime_execution_store = (
        runtime_trace_store if isinstance(runtime_trace_store, ExecutionTraceStore) else None
    )
    runtime_archive = archive
    if runtime_archive is None and state_store is None:
        runtime_archive = runtime_store
    conversation_memory = (
        ConversationMemoryService(
            runtime_archive,
            archive_enabled=runtime_settings.archive_enabled,
            archive_retention_days=runtime_settings.archive_retention_days,
            retrieval_enabled=runtime_settings.history_retrieval_enabled,
            retrieval_limit=runtime_settings.history_retrieval_limit,
            reference_retrieval_limit=runtime_settings.history_reference_retrieval_limit,
        )
        if runtime_archive is not None
        else None
    )
    compilation_service = ChatContextCompilationService(
        runtime_store,
        OpenAIContextCompiler(),
        target_tokens=runtime_settings.target_context_tokens,
        max_tokens=runtime_settings.max_context_tokens,
        recent_turn_reserve=runtime_settings.recent_turn_reserve,
        trace_store=runtime_trace_store,
        trace_capture_content=runtime_settings.trace_capture_content,
        conversation_memory=conversation_memory,
    )
    openclaw_adapter = OpenClawContextAdapter(LifecycleAwareAgentContext(compilation_service))
    proxy = ChatCompletionProxy(
        runtime_transport,
        runtime_settings,
        compilation_service,
        runtime_execution_store,
    )
    responses_proxy = ChatCompletionProxy(
        runtime_responses_transport,
        runtime_settings,
        ResponsesContextCompilationService(compilation_service),
        runtime_execution_store,
        buffered_output_parser=responses_assistant_messages,
        streaming_output_parser=streaming_responses_assistant_messages,
        buffered_usage_parser=responses_usage,
        streaming_usage_parser=streaming_responses_usage,
        streaming_terminal_detector=streaming_responses_terminal,
        protocol="responses",
    )
    ingestion_service = StructuredContextIngestionService(runtime_store)
    context_management = ContextManagementService(runtime_store)
    trace_inspection = (
        TraceInspectionService(runtime_trace_store, OpenAIContextCompiler())
        if runtime_trace_store is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await runtime_store.initialize()
            if runtime_archive is not None and runtime_archive is not runtime_store:
                await runtime_archive.initialize()
            yield
        finally:
            try:
                if owns_store:
                    await runtime_store.aclose()
            finally:
                try:
                    if owns_transport and isinstance(runtime_transport, HttpxOpenAITransport):
                        await runtime_transport.aclose()
                finally:
                    if owns_responses_transport and isinstance(
                        runtime_responses_transport, HttpxOpenAITransport
                    ):
                        await runtime_responses_transport.aclose()

    app = FastAPI(title="CtxTTL", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/models")
    async def models(request: Request) -> Response:
        """Relay the upstream model catalog used by Codex capability discovery."""

        if runtime_model_catalog_transport is None:
            return _error_response(
                "model catalog transport is not configured",
                status_code=501,
                code="model_catalog_unavailable",
            )
        try:
            upstream = await runtime_model_catalog_transport.list_models(
                list(request.query_params.multi_items()),
                request.headers,
            )
            return Response(
                content=upstream.body,
                status_code=upstream.status_code,
                headers=_response_headers(upstream.headers),
            )
        except UpstreamConnectionError as error:
            return _error_response(str(error), status_code=502, code="upstream_unavailable")

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        try:
            payload = await _json_object(request)
            return await _proxy_response(proxy, payload, request.headers)
        except InvalidRequestBody as error:
            return _error_response(str(error), status_code=400, code="invalid_json")
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except CompilationError as error:
            return _error_response(str(error), status_code=400, code="invalid_context")
        except ArchiveConflict as error:
            return _error_response(str(error), status_code=409, code="request_conflict")
        except UpstreamConnectionError as error:
            return _error_response(str(error), status_code=502, code="upstream_unavailable")

    @app.post("/v1/responses")
    async def responses(request: Request) -> Response:
        """Compile explicit Responses input and relay the upstream response losslessly."""

        try:
            payload = await _json_object(request)
            return await _proxy_response(responses_proxy, payload, request.headers)
        except InvalidRequestBody as error:
            return _error_response(str(error), status_code=400, code="invalid_json")
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except CompilationError as error:
            return _error_response(str(error), status_code=400, code="invalid_context")
        except ArchiveConflict as error:
            return _error_response(str(error), status_code=409, code="request_conflict")
        except UpstreamConnectionError as error:
            return _error_response(str(error), status_code=502, code="upstream_unavailable")

    @app.post("/v1/context/compile")
    async def compile_context(request: Request) -> JSONResponse:
        """Compile one provider payload without calling the upstream model."""

        try:
            payload = await _json_object(request)
            identity = _required_identity(request, runtime_settings)
            request_id = resolve_request_id(request.headers, settings=runtime_settings)
            compiled = await compilation_service.compile(
                payload,
                identity,
                request_id=request_id,
            )
            result = compiled.compilation
            return JSONResponse(
                headers={
                    "X-CtxTTL-Request-ID": request_id,
                    "X-CtxTTL-Trace-ID": compiled.trace.id,
                },
                content={
                    "payload": compiled.payload,
                    "trace_id": compiled.trace.id,
                    "request_id": request_id,
                    "metrics": {
                        "source_message_tokens": result.source_message_tokens,
                        "compiled_message_tokens": result.estimated_tokens,
                        "selected_context_ids": list(result.selected_context_ids),
                        "token_ledger": compiled.trace.token_ledger.model_dump()
                        if compiled.trace.token_ledger is not None
                        else None,
                        "compilation_timing": compiled.trace.compilation_timing.model_dump()
                        if compiled.trace.compilation_timing is not None
                        else None,
                    },
                },
            )
        except InvalidRequestBody as error:
            return _error_response(str(error), status_code=400, code="invalid_json")
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except CompilationError as error:
            return _error_response(str(error), status_code=400, code="invalid_context")
        except ArchiveConflict as error:
            return _error_response(str(error), status_code=409, code="request_conflict")

    @app.post("/v1/integrations/openclaw/chat/completions")
    async def openclaw_chat_completions(request: Request) -> Response:
        """Proxy standard OpenAI requests after mapping OpenClaw runtime coordinates."""

        try:
            payload = await _json_object(request)
            agent_id = request.headers.get("X-OpenClaw-Agent-ID")
            session_key = request.headers.get("X-OpenClaw-Session-Key")
            context = OpenClawCompilationInput(
                payload=payload,
                agent_id=agent_id or "",
                session_key=session_key or "",
                channel_id=request.headers.get("X-OpenClaw-Channel-ID"),
                account_id=request.headers.get("X-OpenClaw-Account-ID"),
                sender_id=request.headers.get("X-OpenClaw-Sender-ID"),
                task_key=request.headers.get("X-OpenClaw-Task-Key"),
                turn_key=request.headers.get("X-OpenClaw-Turn-Key"),
                request_key=request.headers.get("X-OpenClaw-Request-Key"),
            )
            header_inactive_turns = openclaw_adapter.parse_inactive_turns(
                request.headers.get("X-OpenClaw-Inactive-Turn-Keys")
            )
            payload, inline_inactive_turns = openclaw_adapter.extract_inline_lifecycle_with_reasons(
                payload
            )
            inactive_turns = tuple(dict.fromkeys((*header_inactive_turns, *inline_inactive_turns)))
            lifecycle = openclaw_adapter.apply_message_lifecycle_with_report(
                payload, inactive_turns
            )
            lifecycle_payload = lifecycle.payload
            adapted = openclaw_adapter.request(lifecycle_payload, context.turn_context())
            adapted_headers = dict(request.headers)
            adapted_headers[runtime_settings.session_header] = adapted.session_id
            adapted_headers[runtime_settings.history_complete_header] = "true"
            for header, value in (
                (runtime_settings.user_header, adapted.user_id),
                (runtime_settings.task_header, adapted.task_id),
                (runtime_settings.agent_header, adapted.agent_id),
                (runtime_settings.project_header, adapted.project_id),
                (runtime_settings.turn_header, adapted.turn_id),
                (runtime_settings.request_header, adapted.request_id),
            ):
                if value is not None:
                    adapted_headers[header] = value
            return await _proxy_response(
                proxy,
                lifecycle_payload,
                adapted_headers,
                lifecycle.exclusion,
            )
        except InvalidRequestBody as error:
            return _error_response(str(error), status_code=400, code="invalid_json")
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except CompilationError as error:
            return _error_response(str(error), status_code=400, code="invalid_context")
        except ValueError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except ArchiveConflict as error:
            return _error_response(str(error), status_code=409, code="request_conflict")
        except UpstreamConnectionError as error:
            return _error_response(str(error), status_code=502, code="upstream_unavailable")

    @app.post("/v1/integrations/openclaw/compile")
    async def compile_openclaw_context(
        integration_request: OpenClawCompilationInput,
    ) -> JSONResponse:
        """Compile an OpenClaw request without owning or executing its Agent loop."""

        try:
            compiled = await openclaw_adapter.compile(
                integration_request.payload,
                integration_request.turn_context(),
            )
            result = compiled.compilation
            return JSONResponse(
                content={
                    "payload": compiled.payload,
                    "trace_id": compiled.trace.id,
                    "metrics": {
                        "source_message_tokens": result.source_message_tokens,
                        "compiled_message_tokens": result.estimated_tokens,
                        "selected_context_ids": list(result.selected_context_ids),
                        "token_ledger": compiled.trace.token_ledger.model_dump()
                        if compiled.trace.token_ledger is not None
                        else None,
                        "compilation_timing": compiled.trace.compilation_timing.model_dump()
                        if compiled.trace.compilation_timing is not None
                        else None,
                    },
                }
            )
        except CompilationError as error:
            return _error_response(str(error), status_code=400, code="invalid_context")
        except ValueError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")

    @app.post("/v1/context/items", status_code=201)
    async def ingest_context(item: StructuredContextInput, request: Request) -> JSONResponse:
        """Persist an explicit user assertion; corrections name the item they supersede."""

        try:
            resolution = resolve_identity(request.headers, settings=runtime_settings)
            if resolution.identity is None:
                raise MissingSessionIdentity(
                    f"missing required identity header: {runtime_settings.session_header}"
                )
            request_id = resolve_request_id(request.headers, settings=runtime_settings)
            result = await ingestion_service.ingest(
                item,
                resolution.identity,
                request_id=request_id,
            )
            return JSONResponse(
                status_code=201,
                headers={"X-CtxTTL-Request-ID": request_id},
                content={
                    "applied": result.applied,
                    "event": result.event.model_dump(mode="json"),
                    "item": result.item.model_dump(mode="json") if result.item else None,
                },
            )
        except (IdentityError, StructuredIngestionError) as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except ContextNotFound as error:
            return _error_response(str(error), status_code=404, code="context_not_found")
        except RequestConflict as error:
            return _error_response(str(error), status_code=409, code="request_conflict")
        except ContextConflict as error:
            return _error_response(str(error), status_code=409, code="context_conflict")

    @app.get("/v1/context/items")
    async def list_context_items(
        request: Request,
        status: ContextStatus | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> JSONResponse:
        try:
            identity = _required_identity(request, runtime_settings)
            items = await context_management.list_items(identity, status=status, limit=limit)
            return JSONResponse(content={"items": [item.model_dump(mode="json") for item in items]})
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")

    @app.get("/v1/context/items/{context_id}")
    async def get_context_item(context_id: str, request: Request) -> JSONResponse:
        try:
            identity = _required_identity(request, runtime_settings)
            item = await context_management.get_item(context_id, identity)
            return JSONResponse(content={"item": item.model_dump(mode="json")})
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except ContextNotFound as error:
            return _error_response(str(error), status_code=404, code="context_not_found")

    @app.post("/v1/context/items/{context_id}/retract")
    async def retract_context_item(
        context_id: str,
        lifecycle: LifecycleInput,
        request: Request,
    ) -> JSONResponse:
        return await _end_context_lifecycle(
            context_management,
            context_id,
            lifecycle,
            request,
            runtime_settings,
            expire=False,
        )

    @app.post("/v1/context/items/{context_id}/expire")
    async def expire_context_item(
        context_id: str,
        lifecycle: LifecycleInput,
        request: Request,
    ) -> JSONResponse:
        return await _end_context_lifecycle(
            context_management,
            context_id,
            lifecycle,
            request,
            runtime_settings,
            expire=True,
        )

    @app.get("/v1/traces")
    async def list_traces(
        request: Request,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> JSONResponse:
        if trace_inspection is None:
            return _error_response(
                "trace store is unavailable", status_code=503, code="unavailable"
            )
        try:
            identity = _required_identity(request, runtime_settings)
            traces = await trace_inspection.list_traces(identity, limit=limit)
            return JSONResponse(
                content={"traces": [trace.model_dump(mode="json") for trace in traces]}
            )
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")

    @app.get("/v1/traces/{trace_id}")
    async def get_trace(trace_id: str, request: Request) -> JSONResponse:
        if trace_inspection is None:
            return _error_response(
                "trace store is unavailable", status_code=503, code="unavailable"
            )
        try:
            identity = _required_identity(request, runtime_settings)
            trace = await trace_inspection.get_trace(trace_id, identity)
            return JSONResponse(content={"trace": trace.model_dump(mode="json")})
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except TraceNotFound as error:
            return _error_response(str(error), status_code=404, code="trace_not_found")

    @app.get("/v1/traces/{trace_id}/execution")
    async def get_trace_execution(trace_id: str, request: Request) -> JSONResponse:
        if trace_inspection is None or runtime_execution_store is None:
            return _error_response(
                "execution trace store is unavailable", status_code=503, code="unavailable"
            )
        try:
            identity = _required_identity(request, runtime_settings)
            await trace_inspection.get_trace(trace_id, identity)
            execution = await runtime_execution_store.get_execution(trace_id)
            if execution is None:
                return _error_response(
                    f"execution trace not found: {trace_id}",
                    status_code=404,
                    code="execution_trace_not_found",
                )
            return JSONResponse(content={"execution": execution.model_dump(mode="json")})
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except TraceNotFound as error:
            return _error_response(str(error), status_code=404, code="trace_not_found")

    @app.post("/v1/traces/{trace_id}/replay")
    async def replay_trace(trace_id: str, request: Request) -> JSONResponse:
        if trace_inspection is None:
            return _error_response(
                "trace store is unavailable", status_code=503, code="unavailable"
            )
        try:
            identity = _required_identity(request, runtime_settings)
            replay = await trace_inspection.replay(trace_id, identity)
            return JSONResponse(content={"replay": replay.model_dump(mode="json")})
        except IdentityError as error:
            return _error_response(str(error), status_code=400, code="invalid_identity")
        except TraceNotFound as error:
            return _error_response(str(error), status_code=404, code="trace_not_found")
        except ReplayUnavailable as error:
            return _error_response(str(error), status_code=409, code="replay_unavailable")

    return app


def _required_identity(request: Request, settings: Settings) -> RequestIdentity:
    resolution = resolve_identity(request.headers, settings=settings)
    if resolution.identity is None:
        raise MissingSessionIdentity(f"missing required identity header: {settings.session_header}")
    return resolution.identity


async def _end_context_lifecycle(
    service: ContextManagementService,
    context_id: str,
    lifecycle: LifecycleInput,
    request: Request,
    settings: Settings,
    *,
    expire: bool,
) -> JSONResponse:
    try:
        identity = _required_identity(request, settings)
        result = (
            await service.expire(context_id, identity, lifecycle)
            if expire
            else await service.retract(context_id, identity, lifecycle)
        )
        return JSONResponse(
            content={
                "applied": result.applied,
                "event": result.event.model_dump(mode="json"),
            }
        )
    except IdentityError as error:
        return _error_response(str(error), status_code=400, code="invalid_identity")
    except ContextNotFound as error:
        return _error_response(str(error), status_code=404, code="context_not_found")
    except ContextConflict as error:
        return _error_response(str(error), status_code=409, code="context_conflict")
