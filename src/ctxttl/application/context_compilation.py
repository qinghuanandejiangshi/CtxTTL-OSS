"""Application orchestration for request-scoped context compilation."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Any

from ctxttl.application.context_management import ContextManagementService
from ctxttl.application.context_repository import ContextStateStore
from ctxttl.application.conversation_memory import ConversationMemoryService
from ctxttl.archive import MessageDirection
from ctxttl.compiler import (
    CompilationResult,
    DecisionReason,
    OpenAIContextCompiler,
    ProtocolViolation,
    TokenBudgetExceeded,
)
from ctxttl.identity import RequestIdentity, reachable_owner_keys
from ctxttl.models import Authority, ContextItem
from ctxttl.observability import (
    CompilationTiming,
    CompilationTrace,
    CompilationTraceStore,
    PreCompilationExclusion,
    TokenSavingsLedger,
    TraceDecision,
)

COMPILER_REVISION = "openai-v4"


@dataclass(frozen=True, slots=True)
class CompiledChatPayload:
    """Lossless provider payload with its compilation result."""

    payload: dict[str, Any]
    compilation: CompilationResult
    trace: CompilationTrace


class ChatContextCompilationService:
    """Load isolated state and invoke the pure compiler."""

    def __init__(
        self,
        store: ContextStateStore,
        compiler: OpenAIContextCompiler,
        *,
        target_tokens: int,
        max_tokens: int,
        recent_turn_reserve: int,
        trace_store: CompilationTraceStore | None = None,
        trace_capture_content: bool = False,
        conversation_memory: ConversationMemoryService | None = None,
    ) -> None:
        self._context_management = ContextManagementService(store)
        self._compiler = compiler
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens
        self._recent_turn_reserve = recent_turn_reserve
        self._trace_store = trace_store
        self._trace_capture_content = trace_capture_content
        self._conversation_memory = conversation_memory

    async def compile(
        self,
        payload: Mapping[str, Any],
        identity: RequestIdentity,
        *,
        request_id: str | None = None,
        retrieve_history: bool = True,
        pre_compilation_exclusion: PreCompilationExclusion | None = None,
    ) -> CompiledChatPayload:
        raw_messages = payload.get("messages", [])
        if not isinstance(raw_messages, list):
            raise ProtocolViolation("messages must be an array")

        started_ns = perf_counter_ns()
        pre_compilation_exclusion = pre_compilation_exclusion or PreCompilationExclusion()
        state_started_ns = perf_counter_ns()
        owner_keys = reachable_owner_keys(identity)
        active_items = await self._context_management.list_for_compilation(identity)
        state_finished_ns = perf_counter_ns()
        retrieval_started_ns = state_finished_ns
        retrieved_items = (
            await self._conversation_memory.retrieve(raw_messages, identity, owner_keys)
            if self._conversation_memory is not None and retrieve_history
            else []
        )
        retrieval_finished_ns = perf_counter_ns()
        candidate_items = [*active_items, *retrieved_items]
        output_reserve = self._output_reserve(payload)
        available_max = self._max_tokens - output_reserve
        if available_max < 1:
            raise TokenBudgetExceeded(
                "requested output tokens leave no room inside max_context_tokens"
            )
        compiler_started_ns = perf_counter_ns()
        result = self._compiler.compile(
            raw_messages,
            candidate_items,
            target_tokens=min(self._target_tokens, available_max),
            max_tokens=available_max,
            recent_turn_reserve=self._recent_turn_reserve,
        )
        compiler_finished_ns = perf_counter_ns()
        compiled_payload = dict(payload)
        compiled_payload["messages"] = list(result.messages)
        validated_messages = [dict(message) for message in raw_messages]
        trace = self._trace(
            payload,
            identity,
            validated_messages,
            candidate_items,
            result,
            request_id=request_id,
            output_reserve=output_reserve,
            pre_compilation_exclusion=pre_compilation_exclusion,
            timing=CompilationTiming(
                context_state_ms=(state_finished_ns - state_started_ns) / 1_000_000,
                history_retrieval_ms=(retrieval_finished_ns - retrieval_started_ns) / 1_000_000,
                compiler_ms=(compiler_finished_ns - compiler_started_ns) / 1_000_000,
                total_ms=(perf_counter_ns() - started_ns) / 1_000_000,
            ),
        )
        if self._trace_store is not None:
            await self._trace_store.record_trace(trace)
        if self._conversation_memory is not None:
            await self._conversation_memory.archive(
                validated_messages,
                identity,
                trace,
                request_id=request_id,
            )
        return CompiledChatPayload(payload=compiled_payload, compilation=result, trace=trace)

    async def archive_output(
        self,
        messages: list[dict[str, Any]],
        identity: RequestIdentity,
        trace: CompilationTrace,
        *,
        request_id: str,
    ) -> None:
        """Archive captured provider output through the same replaceable memory boundary."""

        if self._conversation_memory is not None and messages:
            await self._conversation_memory.archive(
                messages,
                identity,
                trace,
                request_id=request_id,
                direction=MessageDirection.OUTPUT,
            )

    def _trace(
        self,
        payload: Mapping[str, Any],
        identity: RequestIdentity,
        raw_messages: list[dict[str, Any]],
        candidate_items: list[ContextItem],
        result: CompilationResult,
        *,
        request_id: str | None,
        output_reserve: int,
        pre_compilation_exclusion: PreCompilationExclusion,
        timing: CompilationTiming,
    ) -> CompilationTrace:
        try:
            canonical = json.dumps(
                dict(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ProtocolViolation("request payload must be JSON serializable") from error
        model = payload.get("model")
        stream = payload.get("stream", False)
        if not isinstance(stream, bool):
            raise ProtocolViolation("stream must be a boolean")
        source_snapshot = None
        context_snapshot = None
        if self._trace_capture_content:
            source_snapshot = tuple(dict(message) for message in raw_messages)
            context_snapshot = tuple(item.model_dump(mode="json") for item in candidate_items)
        retrieved_items = [
            item for item in candidate_items if item.authority == Authority.RETRIEVED_SOURCE
        ]
        retrieval_ranks = {
            item.id: float(item.metadata["bm25_rank"])
            for item in retrieved_items
            if isinstance(item.metadata.get("bm25_rank"), int | float)
            and not isinstance(item.metadata.get("bm25_rank"), bool)
        }
        token_ledger = self._token_ledger(result, pre_compilation_exclusion)
        return CompilationTrace(
            session_id=identity.session_id,
            user_id=identity.user_id,
            task_id=identity.task_id,
            agent_id=identity.agent_id,
            project_id=identity.project_id,
            turn_id=identity.turn_id,
            request_id=request_id,
            model=model if isinstance(model, str) else None,
            streaming=stream,
            request_fingerprint=hashlib.sha256(canonical).hexdigest(),
            source_message_count=len(raw_messages),
            compiled_message_count=len(result.messages),
            candidate_context_count=len(candidate_items),
            retrieved_context_count=len(retrieved_items),
            selected_retrieved_context_ids=tuple(
                item.id for item in retrieved_items if item.id in result.selected_context_ids
            ),
            retrieval_ranks=retrieval_ranks,
            source_message_tokens=result.source_message_tokens,
            compiled_message_tokens=result.estimated_tokens,
            output_token_reserve=output_reserve,
            target_input_tokens=result.target_tokens,
            max_input_tokens=result.max_tokens,
            recent_turn_reserve=self._recent_turn_reserve,
            compiler_revision=COMPILER_REVISION,
            compilation_duration_ms=timing.total_ms,
            compilation_timing=timing,
            pre_compilation_exclusion=pre_compilation_exclusion,
            token_ledger=token_ledger,
            selected_context_ids=result.selected_context_ids,
            decisions=tuple(
                TraceDecision(
                    candidate_id=decision.candidate_id,
                    candidate_type=decision.candidate_type,
                    included=decision.included,
                    reason=decision.reason.value,
                    token_cost=decision.token_cost,
                    utility=decision.utility,
                )
                for decision in result.decisions
            ),
            source_messages=source_snapshot,
            candidate_context=context_snapshot,
        )

    @staticmethod
    def _token_ledger(
        result: CompilationResult,
        pre_compilation_exclusion: PreCompilationExclusion,
    ) -> TokenSavingsLedger:
        totals: dict[tuple[str, DecisionReason], int] = {}
        for decision in result.decisions:
            key = (decision.candidate_type, decision.reason)
            totals[key] = totals.get(key, 0) + decision.token_cost

        def tokens(candidate_type: str, reason: DecisionReason) -> int:
            return totals.get((candidate_type, reason), 0)

        selected_transcript = tokens("message", DecisionReason.HARD_REQUIRED) + tokens(
            "message", DecisionReason.SOFT_SELECTED
        )
        selected_context = tokens("context", DecisionReason.HARD_REQUIRED) + tokens(
            "context", DecisionReason.SOFT_SELECTED
        )
        message_budget = tokens("message", DecisionReason.BUDGET_EXCEEDED)
        inactive_context = tokens("context", DecisionReason.INACTIVE)
        redundant_context = tokens("context", DecisionReason.REDUNDANT)
        low_relevance_context = tokens("context", DecisionReason.LOW_RELEVANCE)
        context_budget = tokens("context", DecisionReason.BUDGET_EXCEEDED)
        removed = (
            pre_compilation_exclusion.token_count
            + message_budget
            + inactive_context
            + redundant_context
            + low_relevance_context
            + context_budget
        )
        return TokenSavingsLedger(
            pre_compilation_excluded_tokens=pre_compilation_exclusion.token_count,
            source_transcript_tokens=result.source_message_tokens,
            candidate_context_tokens=selected_context
            + inactive_context
            + redundant_context
            + low_relevance_context
            + context_budget,
            selected_transcript_tokens=selected_transcript,
            selected_context_tokens=selected_context,
            message_budget_excluded_tokens=message_budget,
            inactive_context_tokens=inactive_context,
            redundant_context_tokens=redundant_context,
            low_relevance_context_tokens=low_relevance_context,
            context_budget_excluded_tokens=context_budget,
            provider_input_tokens=result.estimated_tokens,
            removed_before_provider_tokens=removed,
        )

    @staticmethod
    def _output_reserve(payload: Mapping[str, Any]) -> int:
        value = payload.get(
            "max_output_tokens",
            payload.get("max_completion_tokens", payload.get("max_tokens", 0)),
        )
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProtocolViolation("max completion tokens must be a non-negative integer")
        return value
