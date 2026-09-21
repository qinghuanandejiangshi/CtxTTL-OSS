"""Ownership-safe trace inspection and deterministic compiler replay."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from ctxttl.application.context_compilation import COMPILER_REVISION
from ctxttl.compiler import OpenAIContextCompiler
from ctxttl.identity import RequestIdentity
from ctxttl.models import ContextItem
from ctxttl.observability import CompilationTrace, CompilationTraceStore


class TraceNotFound(RuntimeError):
    """Raised when a trace is absent or outside the caller's session boundary."""


class ReplayUnavailable(RuntimeError):
    """Raised when a trace lacks the data or compiler revision required for exact replay."""


class TraceReplay(BaseModel):
    """A recomputed compiler result and its comparison with the recorded trace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str
    matches: bool
    mismatches: tuple[str, ...]
    selected_context_ids: tuple[str, ...]
    source_message_tokens: int
    compiled_message_tokens: int
    messages: tuple[dict[str, Any], ...]


class TraceInspectionService:
    """Query and replay traces without crossing explicit session isolation."""

    def __init__(
        self,
        store: CompilationTraceStore,
        compiler: OpenAIContextCompiler,
    ) -> None:
        self._store = store
        self._compiler = compiler

    async def list_traces(
        self,
        identity: RequestIdentity,
        *,
        limit: int = 100,
    ) -> list[CompilationTrace]:
        return await self._store.list_traces(identity.session_id, limit=limit)

    async def get_trace(
        self,
        trace_id: str,
        identity: RequestIdentity,
    ) -> CompilationTrace:
        trace = await self._store.get_trace(trace_id)
        if trace is None or trace.session_id != identity.session_id:
            raise TraceNotFound(f"trace not found: {trace_id}")
        return trace

    async def replay(
        self,
        trace_id: str,
        identity: RequestIdentity,
    ) -> TraceReplay:
        trace = await self.get_trace(trace_id, identity)
        if trace.compiler_revision != COMPILER_REVISION:
            raise ReplayUnavailable("trace compiler revision is unavailable for exact replay")
        if (
            trace.source_messages is None
            or trace.candidate_context is None
            or trace.recent_turn_reserve is None
        ):
            raise ReplayUnavailable(
                "exact replay requires CTXTTL_TRACE_CAPTURE_CONTENT=true at capture time"
            )
        try:
            candidates = [ContextItem.model_validate(item) for item in trace.candidate_context]
        except ValueError as error:
            raise ReplayUnavailable("trace candidate snapshot is invalid") from error
        result = self._compiler.compile(
            list(trace.source_messages),
            candidates,
            target_tokens=trace.target_input_tokens,
            max_tokens=trace.max_input_tokens,
            recent_turn_reserve=trace.recent_turn_reserve,
        )
        mismatches: list[str] = []
        if result.selected_context_ids != trace.selected_context_ids:
            mismatches.append("selected_context_ids")
        if result.source_message_tokens != trace.source_message_tokens:
            mismatches.append("source_message_tokens")
        if result.estimated_tokens != trace.compiled_message_tokens:
            mismatches.append("compiled_message_tokens")
        if len(result.messages) != trace.compiled_message_count:
            mismatches.append("compiled_message_count")
        recorded_decisions = tuple(
            (
                decision.candidate_id,
                decision.candidate_type,
                decision.included,
                decision.reason,
                decision.token_cost,
                decision.utility,
            )
            for decision in trace.decisions
        )
        replayed_decisions = tuple(
            (
                decision.candidate_id,
                decision.candidate_type,
                decision.included,
                decision.reason.value,
                decision.token_cost,
                decision.utility,
            )
            for decision in result.decisions
        )
        if replayed_decisions != recorded_decisions:
            mismatches.append("decisions")
        return TraceReplay(
            trace_id=trace.id,
            matches=not mismatches,
            mismatches=tuple(mismatches),
            selected_context_ids=result.selected_context_ids,
            source_message_tokens=result.source_message_tokens,
            compiled_message_tokens=result.estimated_tokens,
            messages=result.messages,
        )
