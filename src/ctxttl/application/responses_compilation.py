"""Responses API compilation through the stable lifecycle-aware compiler service."""

from collections.abc import Mapping
from typing import Any

from ctxttl.application.context_compilation import (
    ChatContextCompilationService,
    CompiledChatPayload,
)
from ctxttl.application.responses_protocol import ResponsesProtocolAdapter
from ctxttl.identity import RequestIdentity
from ctxttl.observability import CompilationTrace, PreCompilationExclusion


class ResponsesContextCompilationService:
    """Adapt explicit Responses input without duplicating lifecycle or selection policy."""

    def __init__(
        self,
        compilation_service: ChatContextCompilationService,
        adapter: ResponsesProtocolAdapter | None = None,
    ) -> None:
        self._compilation_service = compilation_service
        self._adapter = adapter or ResponsesProtocolAdapter()

    async def compile(
        self,
        payload: Mapping[str, Any],
        identity: RequestIdentity,
        *,
        request_id: str | None = None,
        retrieve_history: bool = True,
        pre_compilation_exclusion: PreCompilationExclusion | None = None,
    ) -> CompiledChatPayload:
        compiler_payload = self._adapter.to_compiler_payload(payload)
        compiled = await self._compilation_service.compile(
            compiler_payload,
            identity,
            request_id=request_id,
            retrieve_history=retrieve_history,
            pre_compilation_exclusion=pre_compilation_exclusion,
        )
        response_payload = self._adapter.from_compiler_payload(payload, compiled.payload)
        return CompiledChatPayload(
            payload=response_payload,
            compilation=compiled.compilation,
            trace=compiled.trace,
        )

    async def archive_output(
        self,
        messages: list[dict[str, Any]],
        identity: RequestIdentity,
        trace: CompilationTrace,
        *,
        request_id: str,
    ) -> None:
        await self._compilation_service.archive_output(
            messages,
            identity,
            trace,
            request_id=request_id,
        )
