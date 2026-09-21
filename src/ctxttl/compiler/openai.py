"""Deterministic context compilation for OpenAI Chat Completions messages."""

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ctxttl.compiler.budget import BudgetCandidate, select_candidates
from ctxttl.compiler.models import (
    CompilationDecision,
    CompilationResult,
    ContextMessageRenderer,
    ContextRelevanceScorer,
    DecisionReason,
    ProtocolViolation,
    TokenBudgetExceeded,
    TokenEstimator,
)
from ctxttl.compiler.relevance import LexicalContextRelevanceScorer, latest_user_query
from ctxttl.compiler.rendering import insert_context_messages, render_context_item
from ctxttl.compiler.tokenization import HeuristicTokenEstimator
from ctxttl.models import (
    Authority,
    ContextApplicability,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextStatus,
)


@dataclass(frozen=True, slots=True)
class _MessageUnit:
    id: str
    indexes: tuple[int, ...]
    messages: tuple[dict[str, Any], ...]
    token_cost: int
    hard: bool
    utility: float


_AUTHORITY_WEIGHT = {
    Authority.SYSTEM: 1.0,
    Authority.DEVELOPER: 0.95,
    Authority.EXPLICIT_USER: 0.9,
    Authority.VERIFIED_TOOL: 0.8,
    Authority.RETRIEVED_SOURCE: 0.65,
    Authority.ASSISTANT_INFERENCE: 0.45,
    Authority.SUMMARY: 0.35,
}

_SCOPE_WEIGHT = {
    ContextScope.TURN: 1.0,
    ContextScope.SESSION: 0.9,
    ContextScope.TASK: 0.85,
    ContextScope.AGENT: 0.8,
    ContextScope.PROJECT: 0.7,
    ContextScope.USER: 0.6,
}


class OpenAIContextCompiler:
    """Compile current messages and active Context IR under explicit constraints."""

    def __init__(
        self,
        estimator: TokenEstimator | None = None,
        relevance_scorer: ContextRelevanceScorer | None = None,
        renderer: ContextMessageRenderer | None = None,
        minimum_context_relevance: float = 0.0,
        relevance_fallback_items: int = 0,
    ) -> None:
        if (
            isinstance(minimum_context_relevance, bool)
            or not isinstance(minimum_context_relevance, (int, float))
            or not math.isfinite(minimum_context_relevance)
            or not 0.0 <= minimum_context_relevance <= 1.0
        ):
            raise ValueError("minimum_context_relevance must be a finite number in [0, 1]")
        if isinstance(relevance_fallback_items, bool) or not isinstance(
            relevance_fallback_items, int
        ):
            raise TypeError("relevance_fallback_items must be an integer")
        if relevance_fallback_items < 0:
            raise ValueError("relevance_fallback_items must be non-negative")
        self._estimator = estimator or HeuristicTokenEstimator()
        self._relevance_scorer = relevance_scorer or LexicalContextRelevanceScorer()
        self._renderer = renderer
        self._minimum_context_relevance = float(minimum_context_relevance)
        self._relevance_fallback_items = relevance_fallback_items

    def compile(
        self,
        messages: Sequence[Mapping[str, Any]],
        context_items: Sequence[ContextItem],
        *,
        target_tokens: int,
        max_tokens: int,
        recent_turn_reserve: int,
    ) -> CompilationResult:
        if target_tokens < 1 or max_tokens < target_tokens:
            raise ValueError("token budgets must satisfy 1 <= target_tokens <= max_tokens")
        if recent_turn_reserve < 1:
            raise ValueError("recent_turn_reserve must be positive")

        normalized = self._normalize_messages(messages)
        units = self._message_units(normalized, recent_turn_reserve)
        candidates, initial_decisions = self._context_candidates(
            context_items,
            latest_user_query(normalized),
        )
        candidates.extend(self._message_candidates(units))

        selected, budget_decisions = select_candidates(
            candidates,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
        )
        selected_units = {
            candidate.payload.id
            for candidate in selected
            if isinstance(candidate.payload, _MessageUnit)
        }
        selected_items = [
            candidate.payload
            for candidate in selected
            if isinstance(candidate.payload, ContextItem)
        ]

        output_messages = [
            message for unit in units if unit.id in selected_units for message in unit.messages
        ]
        if selected_items:
            output_messages = insert_context_messages(
                output_messages,
                selected_items,
                renderer=self._renderer,
            )

        estimated_tokens = sum(self._estimate_message(message) for message in output_messages)
        if estimated_tokens > max_tokens:
            raise TokenBudgetExceeded(
                "rendered mandatory context exceeds max_context_tokens; increase the limit or "
                "reduce mandatory input"
            )
        return CompilationResult(
            messages=tuple(output_messages),
            selected_context_ids=tuple(item.id for item in selected_items),
            source_message_tokens=sum(unit.token_cost for unit in units),
            estimated_tokens=estimated_tokens,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
            decisions=tuple(initial_decisions + budget_decisions),
        )

    def _estimate_message(self, message: Mapping[str, Any]) -> int:
        token_cost = self._estimator.estimate_message(message)
        if isinstance(token_cost, bool) or not isinstance(token_cost, int) or token_cost < 1:
            raise RuntimeError("token estimator must return a positive integer")
        return token_cost

    def estimate_messages(self, messages: Sequence[Mapping[str, Any]]) -> int:
        """Estimate a transcript with the compiler's configured token policy."""

        return sum(self._estimate_message(message) for message in messages)

    def _normalize_messages(
        self,
        messages: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                raise ProtocolViolation(f"messages[{index}] must be an object")
            role = message.get("role")
            if not isinstance(role, str) or not role:
                raise ProtocolViolation(f"messages[{index}].role must be a non-empty string")
            normalized.append(dict(message))
        return normalized

    def _message_units(
        self,
        messages: Sequence[dict[str, Any]],
        recent_turn_reserve: int,
    ) -> list[_MessageUnit]:
        raw_units: list[tuple[tuple[int, ...], tuple[dict[str, Any], ...]]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            role = message["role"]
            if role == "tool":
                raise ProtocolViolation(f"orphan tool result at messages[{index}]")
            tool_ids = self._tool_call_ids(message, index)
            if not tool_ids:
                raw_units.append(((index,), (message,)))
                index += 1
                continue

            indexes = [index]
            grouped = [message]
            seen: set[str] = set()
            cursor = index + 1
            while cursor < len(messages) and messages[cursor]["role"] == "tool":
                tool_id = messages[cursor].get("tool_call_id")
                if not isinstance(tool_id, str) or tool_id not in tool_ids or tool_id in seen:
                    raise ProtocolViolation(f"invalid tool result at messages[{cursor}]")
                seen.add(tool_id)
                indexes.append(cursor)
                grouped.append(messages[cursor])
                cursor += 1
            if seen != tool_ids:
                missing = ", ".join(sorted(tool_ids - seen))
                raise ProtocolViolation(f"tool call result is missing for: {missing}")
            raw_units.append((tuple(indexes), tuple(grouped)))
            index = cursor

        grouped_units: list[tuple[tuple[int, ...], tuple[dict[str, Any], ...]]] = []
        pending_indexes: list[int] = []
        pending_messages: list[dict[str, Any]] = []

        def flush_pending() -> None:
            if pending_indexes:
                grouped_units.append((tuple(pending_indexes), tuple(pending_messages)))
                pending_indexes.clear()
                pending_messages.clear()

        for indexes, grouped in raw_units:
            role = grouped[0]["role"]
            if role in {"system", "developer"}:
                flush_pending()
                grouped_units.append((indexes, grouped))
                continue
            if role == "user":
                flush_pending()
            pending_indexes.extend(indexes)
            pending_messages.extend(grouped)
        flush_pending()

        user_units = [
            position
            for position, (_, grouped) in enumerate(grouped_units)
            if grouped[0]["role"] == "user"
        ]
        cutoff = (
            user_units[-min(recent_turn_reserve, len(user_units))]
            if user_units
            else max(0, len(grouped_units) - 1)
        )
        units: list[_MessageUnit] = []
        denominator = max(1, len(grouped_units) - 1)
        for position, (indexes, grouped) in enumerate(grouped_units):
            role = grouped[0]["role"]
            hard = role in {"system", "developer"} or position >= cutoff
            units.append(
                _MessageUnit(
                    id=f"message:{indexes[0]}",
                    indexes=indexes,
                    messages=grouped,
                    token_cost=sum(self._estimate_message(value) for value in grouped),
                    hard=hard,
                    utility=0.55 + 0.4 * (position / denominator),
                )
            )
        return units

    @staticmethod
    def _tool_call_ids(message: Mapping[str, Any], index: int) -> set[str]:
        calls = message.get("tool_calls")
        if calls is None:
            return set()
        if message.get("role") != "assistant" or not isinstance(calls, list) or not calls:
            raise ProtocolViolation(f"invalid tool_calls at messages[{index}]")
        identifiers: set[str] = set()
        for call in calls:
            if not isinstance(call, Mapping) or not isinstance(call.get("id"), str):
                raise ProtocolViolation(f"tool call at messages[{index}] requires an id")
            identifier = call["id"]
            if identifier in identifiers:
                raise ProtocolViolation(f"duplicate tool call id at messages[{index}]")
            identifiers.add(identifier)
        return identifiers

    def _context_candidates(
        self,
        items: Sequence[ContextItem],
        query: str,
    ) -> tuple[list[BudgetCandidate], list[CompilationDecision]]:
        candidates: list[BudgetCandidate] = []
        decisions: list[CompilationDecision] = []
        relevance_by_id: dict[str, float] = {}
        fingerprints: set[str] = set()
        ordered = sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)
        for item in ordered:
            rendered = self._renderer.render(item) if self._renderer else render_context_item(item)
            token_cost = self._estimate_message(rendered)
            if item.status != ContextStatus.ACTIVE:
                decisions.append(
                    CompilationDecision(
                        item.id, "context", False, DecisionReason.INACTIVE, token_cost, None
                    )
                )
                continue
            fingerprint = self._fingerprint(item)
            if fingerprint in fingerprints:
                decisions.append(
                    CompilationDecision(
                        item.id, "context", False, DecisionReason.REDUNDANT, token_cost, None
                    )
                )
                continue
            fingerprints.add(fingerprint)
            relevance = self._relevance_scorer.score(query, item)
            if not math.isfinite(relevance) or not 0.0 <= relevance <= 1.0:
                raise RuntimeError("context relevance scorer must return a finite value in [0, 1]")
            utility = self._item_utility(item, relevance)
            hard = self._is_hard_context(item)
            relevance_by_id[item.id] = relevance
            candidates.append(
                BudgetCandidate(
                    id=item.id,
                    candidate_type="context",
                    token_cost=token_cost,
                    hard=hard,
                    utility=utility,
                    tie_breaker=(0, item.created_at.isoformat(), item.id),
                    payload=item,
                )
            )
        fallback_ids = {
            candidate.id
            for candidate in sorted(
                (candidate for candidate in candidates if not candidate.hard),
                key=lambda candidate: (
                    relevance_by_id[candidate.id],
                    candidate.utility,
                    candidate.tie_breaker,
                ),
                reverse=True,
            )[: self._relevance_fallback_items]
        }
        eligible: list[BudgetCandidate] = []
        for candidate in candidates:
            if (
                not candidate.hard
                and relevance_by_id[candidate.id] < self._minimum_context_relevance
                and candidate.id not in fallback_ids
            ):
                decisions.append(
                    CompilationDecision(
                        candidate.id,
                        candidate.candidate_type,
                        False,
                        DecisionReason.LOW_RELEVANCE,
                        candidate.token_cost,
                        candidate.utility,
                    )
                )
                continue
            eligible.append(candidate)
        return eligible, decisions

    @staticmethod
    def _fingerprint(item: ContextItem) -> str:
        value = json.dumps(item.value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "|".join((item.kind.value, item.scope.value, item.subject or "", value))

    @staticmethod
    def _is_hard_context(item: ContextItem) -> bool:
        if item.applicability in {
            ContextApplicability.CURRENT_TURN,
            ContextApplicability.REQUIRED,
        }:
            return True
        if item.authority in {Authority.SYSTEM, Authority.DEVELOPER}:
            return True
        if item.authority == Authority.EXPLICIT_USER:
            return (
                item.kind
                in {
                    ContextKind.CONSTRAINT,
                    ContextKind.DECISION,
                    ContextKind.TASK_STATE,
                }
                or item.supersedes is not None
            )
        return item.authority == Authority.VERIFIED_TOOL and item.kind == ContextKind.TASK_STATE

    @staticmethod
    def _item_utility(item: ContextItem, relevance: float) -> float:
        return round(
            item.priority * 0.245
            + item.confidence * 0.175
            + _AUTHORITY_WEIGHT[item.authority] * 0.175
            + _SCOPE_WEIGHT[item.scope] * 0.105
            + relevance * 0.3,
            8,
        )

    @staticmethod
    def _message_candidates(units: Sequence[_MessageUnit]) -> list[BudgetCandidate]:
        return [
            BudgetCandidate(
                id=unit.id,
                candidate_type="message",
                token_cost=unit.token_cost,
                hard=unit.hard,
                utility=unit.utility,
                tie_breaker=(1, f"{unit.indexes[0]:020d}", unit.id),
                payload=unit,
            )
            for unit in units
        ]
