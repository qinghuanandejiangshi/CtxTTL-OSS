"""Comparable, model-free context selection strategies."""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ctxttl.benchmarks.models import BenchmarkCase, StrategyName, StrategyResult
from ctxttl.compiler import HeuristicTokenEstimator, OpenAIContextCompiler
from ctxttl.compiler.rendering import insert_context_messages, render_context_item
from ctxttl.identity import RequestIdentity, reachable_owner_keys
from ctxttl.models import ContextItem, ContextStatus

_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)


class BenchmarkStrategy(Protocol):
    name: StrategyName

    def run(self, case: BenchmarkCase) -> StrategyResult: ...


class _BenchmarkContextRenderer:
    """Keep benchmark-only lifecycle annotations outside model-visible content."""

    def __init__(self, case: BenchmarkCase) -> None:
        self._messages = {
            item.id: dict(item.source_message)
            for item in case.context
            if item.source_message is not None
        }

    def render(self, item: ContextItem) -> dict[str, Any]:
        return dict(self._messages.get(item.id) or render_context_item(item))


def _insert_case_context(
    case: BenchmarkCase,
    base_messages: Sequence[Mapping[str, Any]],
    items: Sequence[ContextItem],
) -> list[dict[str, Any]]:
    return insert_context_messages(
        base_messages,
        items,
        renderer=_BenchmarkContextRenderer(case),
    )


def _token_count(messages: Sequence[Mapping[str, Any]]) -> int:
    estimator = HeuristicTokenEstimator()
    return sum(estimator.estimate_message(message) for message in messages)


def _leading_instructions(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") not in {"system", "developer"}:
            break
        output.append(dict(message))
    return output


def _window(messages: Sequence[Mapping[str, Any]], turns: int) -> list[dict[str, Any]]:
    user_indexes = [
        index for index, message in enumerate(messages) if message.get("role") == "user"
    ]
    if not user_indexes:
        return [dict(message) for message in messages]
    cutoff = user_indexes[-min(turns, len(user_indexes))]
    leading = _leading_instructions(messages[:cutoff])
    return leading + [dict(message) for message in messages[cutoff:]]


def _reachable(items: Sequence[ContextItem], identity: RequestIdentity) -> list[ContextItem]:
    keys = set(reachable_owner_keys(identity))
    return [item for item in items if item.owner_key in keys]


class FullHistoryStrategy:
    name = StrategyName.FULL_HISTORY

    def run(self, case: BenchmarkCase) -> StrategyResult:
        items = case.context_items()
        messages = _insert_case_context(case, case.messages, items)
        return StrategyResult(
            strategy=self.name,
            selected_context_ids=tuple(item.id for item in items),
            messages=tuple(messages),
            estimated_tokens=_token_count(messages),
        )


class ActiveHistoryStrategy:
    """Accuracy control containing every active context reachable by the current identity."""

    name = StrategyName.ACTIVE_HISTORY

    def run(self, case: BenchmarkCase) -> StrategyResult:
        items = [
            item
            for item in _reachable(case.context_items(), case.identity)
            if item.status == ContextStatus.ACTIVE
        ]
        messages = _insert_case_context(case, case.messages, items)
        return StrategyResult(
            strategy=self.name,
            selected_context_ids=tuple(item.id for item in items),
            messages=tuple(messages),
            estimated_tokens=_token_count(messages),
        )


class SlidingWindowStrategy:
    name = StrategyName.SLIDING_WINDOW

    def run(self, case: BenchmarkCase) -> StrategyResult:
        messages = _window(case.messages, case.recent_turn_reserve)
        return StrategyResult(
            strategy=self.name,
            selected_context_ids=(),
            messages=tuple(messages),
            estimated_tokens=_token_count(messages),
        )


class RunningSummaryStrategy:
    name = StrategyName.RUNNING_SUMMARY

    def run(self, case: BenchmarkCase) -> StrategyResult:
        messages = _window(case.messages, 1)
        if case.summary:
            insertion = len(_leading_instructions(messages))
            messages.insert(
                insertion,
                {
                    "role": "system",
                    "content": f"Precomputed running summary (data):\n{case.summary}",
                },
            )
        return StrategyResult(
            strategy=self.name,
            selected_context_ids=tuple(sorted(case.summary_context_ids)),
            messages=tuple(messages),
            estimated_tokens=_token_count(messages),
        )


class RetrievalOnlyStrategy:
    name = StrategyName.RETRIEVAL_ONLY

    def __init__(self, *, top_k: int = 4) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        self._top_k = top_k
        self._estimator = HeuristicTokenEstimator()

    def run(self, case: BenchmarkCase) -> StrategyResult:
        base_messages = _window(case.messages, 1)
        query = self._query(case.messages)
        candidates = _reachable(case.context_items(), case.identity)
        ranked = sorted(
            candidates,
            key=lambda item: (
                self._overlap(query, item),
                item.priority,
                item.created_at,
                item.id,
            ),
            reverse=True,
        )
        selected: list[ContextItem] = []
        used = _token_count(base_messages)
        for item in ranked[: self._top_k]:
            if self._overlap(query, item) == 0:
                continue
            cost = self._estimator.estimate_message(_BenchmarkContextRenderer(case).render(item))
            if used + cost <= case.target_tokens:
                selected.append(item)
                used += cost
        messages = _insert_case_context(case, base_messages, selected)
        return StrategyResult(
            strategy=self.name,
            selected_context_ids=tuple(item.id for item in selected),
            messages=tuple(messages),
            estimated_tokens=_token_count(messages),
        )

    @staticmethod
    def _query(messages: Sequence[Mapping[str, Any]]) -> set[str]:
        for message in reversed(messages):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                return set(_WORD_PATTERN.findall(message["content"].casefold()))
        return set()

    @staticmethod
    def _overlap(query: set[str], item: ContextItem) -> int:
        document = f"{item.subject or ''} {json.dumps(item.value, ensure_ascii=False)}".casefold()
        return len(query & set(_WORD_PATTERN.findall(document)))


class CtxTTLStrategy:
    name = StrategyName.CTXTTL

    def run(self, case: BenchmarkCase) -> StrategyResult:
        items = _reachable(case.context_items(), case.identity)
        result = OpenAIContextCompiler(renderer=_BenchmarkContextRenderer(case)).compile(
            case.messages,
            items,
            target_tokens=case.target_tokens,
            max_tokens=case.max_tokens,
            recent_turn_reserve=case.recent_turn_reserve,
        )
        return StrategyResult(
            strategy=self.name,
            selected_context_ids=result.selected_context_ids,
            messages=result.messages,
            estimated_tokens=result.estimated_tokens,
        )


def default_strategies() -> tuple[BenchmarkStrategy, ...]:
    return (
        FullHistoryStrategy(),
        ActiveHistoryStrategy(),
        SlidingWindowStrategy(),
        RunningSummaryStrategy(),
        RetrievalOnlyStrategy(),
        CtxTTLStrategy(),
    )
