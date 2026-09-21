"""Provider-neutral application port for Agent context compilation."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ctxttl.application.context_compilation import (
    ChatContextCompilationService,
    CompiledChatPayload,
)
from ctxttl.identity import RequestIdentity
from ctxttl.observability import PreCompilationExclusion


@dataclass(frozen=True, slots=True)
class AgentContextRequest:
    """One Agent model request with explicit lifecycle isolation coordinates."""

    payload: Mapping[str, Any]
    session_id: str
    user_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    pre_compilation_exclusion: PreCompilationExclusion | None = None


class AgentContextPort(Protocol):
    """Agent-facing boundary; framework adapters depend only on this contract."""

    async def compile(self, request: AgentContextRequest) -> CompiledChatPayload: ...


class LifecycleAwareAgentContext:
    """Adapt the existing lifecycle compiler to the generic Agent port."""

    def __init__(self, compilation_service: ChatContextCompilationService) -> None:
        self._compilation_service = compilation_service

    async def compile(self, request: AgentContextRequest) -> CompiledChatPayload:
        identity = RequestIdentity(
            session_id=request.session_id,
            user_id=request.user_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            project_id=request.project_id,
            turn_id=request.turn_id,
        )
        return await self._compilation_service.compile(
            request.payload,
            identity,
            request_id=request.request_id,
            pre_compilation_exclusion=request.pre_compilation_exclusion,
        )
