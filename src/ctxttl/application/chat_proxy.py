"""Chat Completions proxy use case, independent from HTTP and provider implementations."""

import logging
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter_ns
from typing import Any, Protocol

from ctxttl.application.chat_capture import (
    buffered_assistant_messages,
    streaming_assistant_messages,
)
from ctxttl.application.context_compilation import (
    CompiledChatPayload,
)
from ctxttl.application.ports import (
    BufferedProviderResponse,
    ChatCompletionTransport,
    StreamingProviderResponse,
)
from ctxttl.application.provider_capture import (
    ProviderUsage,
    chat_usage,
    streaming_chat_terminal,
    streaming_chat_usage,
)
from ctxttl.config import Settings
from ctxttl.identity import (
    RequestIdentity,
    ResolutionAction,
    resolve_identity,
    resolve_request_id,
)
from ctxttl.observability import (
    CompilationTrace,
    ExecutionOutcome,
    ExecutionTrace,
    ExecutionTraceStore,
    PreCompilationExclusion,
)

logger = logging.getLogger(__name__)


class ProxyMode(StrEnum):
    COMPILE = "compile"
    PASSTHROUGH = "passthrough"


class ContextCompilationPort(Protocol):
    """Protocol-neutral compilation boundary consumed by the shared proxy flow."""

    async def compile(
        self,
        payload: Mapping[str, Any],
        identity: RequestIdentity,
        *,
        request_id: str | None = None,
        retrieve_history: bool = True,
        pre_compilation_exclusion: PreCompilationExclusion | None = None,
    ) -> CompiledChatPayload: ...

    async def archive_output(
        self,
        messages: list[dict[str, Any]],
        identity: RequestIdentity,
        trace: CompilationTrace,
        *,
        request_id: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PreparedChatRequest:
    """A request with an explicit decision about context processing."""

    payload: Mapping[str, Any]
    request_headers: Mapping[str, str]
    mode: ProxyMode
    identity: RequestIdentity | None
    request_id: str
    pre_compilation_exclusion: PreCompilationExclusion | None = None


class ChatCompletionProxy:
    """Coordinate identity, optional context compilation, and provider transport."""

    def __init__(
        self,
        transport: ChatCompletionTransport,
        settings: Settings,
        compilation_service: ContextCompilationPort | None = None,
        execution_store: ExecutionTraceStore | None = None,
        buffered_output_parser: Callable[[bytes, int], list[dict[str, Any]]] = (
            buffered_assistant_messages
        ),
        streaming_output_parser: Callable[[bytes, int], list[dict[str, Any]]] = (
            streaming_assistant_messages
        ),
        buffered_usage_parser: Callable[[bytes, int], ProviderUsage | None] = chat_usage,
        streaming_usage_parser: Callable[[bytes, int], ProviderUsage | None] = (
            streaming_chat_usage
        ),
        streaming_terminal_detector: Callable[[bytes, int], bool] = streaming_chat_terminal,
        protocol: str = "chat_completions",
    ) -> None:
        self._transport = transport
        self._settings = settings
        self._compilation_service = compilation_service
        self._execution_store = execution_store
        self._buffered_output_parser = buffered_output_parser
        self._streaming_output_parser = streaming_output_parser
        self._buffered_usage_parser = buffered_usage_parser
        self._streaming_usage_parser = streaming_usage_parser
        self._streaming_terminal_detector = streaming_terminal_detector
        self._protocol = protocol

    def prepare(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
        pre_compilation_exclusion: PreCompilationExclusion | None = None,
    ) -> PreparedChatRequest:
        request_user = payload.get("user")
        resolution = resolve_identity(
            request_headers,
            request_user=request_user if isinstance(request_user, str) else None,
            settings=self._settings,
        )
        mode = (
            ProxyMode.COMPILE
            if resolution.action == ResolutionAction.COMPILE
            else ProxyMode.PASSTHROUGH
        )
        return PreparedChatRequest(
            payload=payload,
            request_headers=request_headers,
            mode=mode,
            identity=resolution.identity,
            request_id=resolve_request_id(request_headers, settings=self._settings),
            pre_compilation_exclusion=pre_compilation_exclusion,
        )

    async def complete(self, request: PreparedChatRequest) -> BufferedProviderResponse:
        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        payload, compiled = await self._payload_for(request)
        compilation_finished_ns = perf_counter_ns()
        try:
            response = await self._transport.complete(payload, request.request_headers)
        except Exception:
            await self._record_execution_safely(
                request,
                compiled,
                started_at=started_at,
                started_ns=started_ns,
                compilation_finished_ns=compilation_finished_ns,
                outcome=ExecutionOutcome.UPSTREAM_ERROR,
            )
            raise
        upstream_finished_ns = perf_counter_ns()
        response = replace(
            response,
            compilation_duration_ms=(compilation_finished_ns - started_ns) / 1_000_000,
            upstream_response_start_ms=(upstream_finished_ns - compilation_finished_ns) / 1_000_000,
            proxy_response_start_ms=(upstream_finished_ns - started_ns) / 1_000_000,
        )
        if compiled is not None and request.identity is not None:
            messages = self._buffered_output_parser(response.body, response.status_code)
            usage = self._buffered_usage_parser(response.body, response.status_code)
            await self._archive_output_safely(messages, request, compiled.trace)
            await self._record_execution_safely(
                request,
                compiled,
                started_at=started_at,
                started_ns=started_ns,
                compilation_finished_ns=compilation_finished_ns,
                upstream_finished_ns=upstream_finished_ns,
                outcome=ExecutionOutcome.COMPLETED,
                status_code=response.status_code,
                provider_usage=usage,
            )
            return replace(response, trace_id=compiled.trace.id)
        return response

    async def stream(self, request: PreparedChatRequest) -> StreamingProviderResponse:
        started_at = datetime.now(UTC)
        started_ns = perf_counter_ns()
        payload, compiled = await self._payload_for(request)
        compilation_finished_ns = perf_counter_ns()
        try:
            response = await self._transport.stream(payload, request.request_headers)
        except Exception:
            await self._record_execution_safely(
                request,
                compiled,
                started_at=started_at,
                started_ns=started_ns,
                compilation_finished_ns=compilation_finished_ns,
                outcome=ExecutionOutcome.UPSTREAM_ERROR,
            )
            raise
        upstream_finished_ns = perf_counter_ns()
        timing = {
            "compilation_duration_ms": (compilation_finished_ns - started_ns) / 1_000_000,
            "upstream_response_start_ms": (upstream_finished_ns - compilation_finished_ns)
            / 1_000_000,
            "proxy_response_start_ms": (upstream_finished_ns - started_ns) / 1_000_000,
        }
        if compiled is None or request.identity is None:
            return replace(response, **timing)

        async def captured_body() -> AsyncIterator[bytes]:
            captured = bytearray()
            capture_complete = True
            exhausted = False
            terminal_recorded = False
            try:
                async for chunk in response.body:
                    if capture_complete:
                        if len(captured) + len(chunk) <= self._settings.response_capture_max_bytes:
                            captured.extend(chunk)
                        else:
                            capture_complete = False
                            captured.clear()
                    if (
                        capture_complete
                        and not terminal_recorded
                        and self._streaming_terminal_detector(
                            bytes(captured),
                            response.status_code,
                        )
                    ):
                        messages = self._streaming_output_parser(
                            bytes(captured),
                            response.status_code,
                        )
                        await self._archive_output_safely(messages, request, compiled.trace)
                        usage = self._streaming_usage_parser(
                            bytes(captured),
                            response.status_code,
                        )
                        await self._record_execution_safely(
                            request,
                            compiled,
                            started_at=started_at,
                            started_ns=started_ns,
                            compilation_finished_ns=compilation_finished_ns,
                            upstream_finished_ns=upstream_finished_ns,
                            outcome=ExecutionOutcome.COMPLETED,
                            status_code=response.status_code,
                            stream_finished_ns=perf_counter_ns(),
                            provider_usage=usage,
                        )
                        terminal_recorded = True
                    yield chunk
                exhausted = True
            finally:
                if not terminal_recorded:
                    if exhausted and capture_complete:
                        messages = self._streaming_output_parser(
                            bytes(captured),
                            response.status_code,
                        )
                        await self._archive_output_safely(messages, request, compiled.trace)
                        usage = self._streaming_usage_parser(bytes(captured), response.status_code)
                    else:
                        usage = None
                    await self._record_execution_safely(
                        request,
                        compiled,
                        started_at=started_at,
                        started_ns=started_ns,
                        compilation_finished_ns=compilation_finished_ns,
                        upstream_finished_ns=upstream_finished_ns,
                        outcome=(
                            ExecutionOutcome.COMPLETED
                            if exhausted
                            else ExecutionOutcome.INTERRUPTED
                        ),
                        status_code=response.status_code,
                        stream_finished_ns=perf_counter_ns(),
                        provider_usage=usage,
                    )

        return replace(
            response,
            body=captured_body(),
            trace_id=compiled.trace.id,
            **timing,
        )

    async def _payload_for(
        self, request: PreparedChatRequest
    ) -> tuple[Mapping[str, Any], CompiledChatPayload | None]:
        if (
            request.mode != ProxyMode.COMPILE
            or request.identity is None
            or self._compilation_service is None
        ):
            return request.payload, None
        compiled = await self._compilation_service.compile(
            request.payload,
            request.identity,
            request_id=request.request_id,
            retrieve_history=not self._history_is_complete(request.request_headers),
            pre_compilation_exclusion=request.pre_compilation_exclusion,
        )
        return compiled.payload, compiled

    def _history_is_complete(self, headers: Mapping[str, str]) -> bool:
        value = headers.get(self._settings.history_complete_header)
        if value is None:
            value = headers.get(self._settings.history_complete_header.lower())
        return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"}

    async def _archive_output_safely(
        self,
        messages: list[dict[str, Any]],
        request: PreparedChatRequest,
        trace: CompilationTrace,
    ) -> None:
        if not messages or request.identity is None or self._compilation_service is None:
            return
        try:
            await self._compilation_service.archive_output(
                messages,
                request.identity,
                trace,
                request_id=request.request_id,
            )
        except Exception:
            logger.exception(
                "failed to archive provider output",
                extra={"ctxttl_trace_id": trace.id, "ctxttl_request_id": request.request_id},
            )

    async def _record_execution_safely(
        self,
        request: PreparedChatRequest,
        compiled: CompiledChatPayload | None,
        *,
        started_at: datetime,
        started_ns: int,
        compilation_finished_ns: int,
        outcome: ExecutionOutcome,
        upstream_finished_ns: int | None = None,
        status_code: int | None = None,
        stream_finished_ns: int | None = None,
        provider_usage: ProviderUsage | None = None,
    ) -> None:
        if self._execution_store is None or compiled is None:
            return
        finished_ns = stream_finished_ns or upstream_finished_ns or perf_counter_ns()
        execution = ExecutionTrace(
            trace_id=compiled.trace.id,
            session_id=compiled.trace.session_id,
            request_id=request.request_id,
            protocol=self._protocol,
            streaming=compiled.trace.streaming,
            outcome=outcome,
            status_code=status_code,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            compilation_duration_ms=(compilation_finished_ns - started_ns) / 1_000_000,
            upstream_call_duration_ms=(
                (upstream_finished_ns - compilation_finished_ns) / 1_000_000
                if upstream_finished_ns is not None
                else None
            ),
            stream_duration_ms=(
                (stream_finished_ns - upstream_finished_ns) / 1_000_000
                if stream_finished_ns is not None and upstream_finished_ns is not None
                else None
            ),
            proxy_total_duration_ms=(finished_ns - started_ns) / 1_000_000,
            provider_input_tokens=(provider_usage.input_tokens if provider_usage else None),
            provider_cached_input_tokens=(
                provider_usage.cached_input_tokens if provider_usage else None
            ),
            provider_output_tokens=(provider_usage.output_tokens if provider_usage else None),
        )
        try:
            await self._execution_store.record_execution(execution)
            logger.info(
                "recorded provider execution",
                extra={
                    "event": "provider_execution_recorded",
                    "ctxttl_trace_id": compiled.trace.id,
                    "ctxttl_request_id": request.request_id,
                    "protocol": self._protocol,
                    "outcome": outcome.value,
                    "status_code": status_code,
                    "proxy_total_duration_ms": execution.proxy_total_duration_ms,
                    "provider_input_tokens": execution.provider_input_tokens,
                    "provider_cached_input_tokens": execution.provider_cached_input_tokens,
                    "provider_output_tokens": execution.provider_output_tokens,
                },
            )
        except Exception:
            logger.exception(
                "failed to persist provider execution timing",
                extra={
                    "ctxttl_trace_id": compiled.trace.id,
                    "ctxttl_request_id": request.request_id,
                },
            )
