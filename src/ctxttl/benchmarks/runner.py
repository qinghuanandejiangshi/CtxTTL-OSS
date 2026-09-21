"""Reproducible benchmark execution and metric aggregation."""

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Sequence
from statistics import fmean
from time import perf_counter_ns

from ctxttl.benchmarks.models import (
    BenchmarkCase,
    BenchmarkReport,
    CaseResult,
    StrategyResult,
    StrategySummary,
)
from ctxttl.benchmarks.strategies import BenchmarkStrategy, default_strategies
from ctxttl.models import ContextStatus


class BenchmarkRunner:
    """Run every strategy twice to measure selection quality and stability."""

    def __init__(self, strategies: Sequence[BenchmarkStrategy] | None = None) -> None:
        self._strategies = tuple(strategies or default_strategies())
        if not self._strategies:
            raise ValueError("at least one benchmark strategy is required")
        names = [strategy.name for strategy in self._strategies]
        if len(names) != len(set(names)):
            raise ValueError("benchmark strategy names must be unique")

    def run(
        self,
        cases: Sequence[BenchmarkCase],
        *,
        input_price_per_million: float | None = None,
    ) -> BenchmarkReport:
        if not cases:
            raise ValueError("at least one benchmark case is required")
        case_ids = [case.id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("benchmark case IDs must be unique")
        if input_price_per_million is not None and (
            isinstance(input_price_per_million, bool)
            or input_price_per_million < 0
            or not math.isfinite(input_price_per_million)
        ):
            raise ValueError("input token price must be finite and non-negative")

        case_results: list[CaseResult] = []
        for case in cases:
            for strategy in self._strategies:
                started_ns = perf_counter_ns()
                first = strategy.run(case)
                duration_ms = (perf_counter_ns() - started_ns) / 1_000_000
                replay = strategy.run(case)
                stable = (
                    first.selected_context_ids == replay.selected_context_ids
                    and first.messages == replay.messages
                    and first.estimated_tokens == replay.estimated_tokens
                )
                case_results.append(self._case_result(case, first, duration_ms, stable))

        grouped: dict[str, list[CaseResult]] = defaultdict(list)
        for result in case_results:
            grouped[result.strategy.value].append(result)
        summaries = tuple(
            self._summary(results, input_price_per_million)
            for _, results in sorted(grouped.items())
        )
        return BenchmarkReport(
            dataset_sha256=benchmark_dataset_hash(cases),
            dataset_case_count=len(cases),
            summaries=summaries,
            cases=tuple(case_results),
        )

    @staticmethod
    def _case_result(
        case: BenchmarkCase,
        result: StrategyResult,
        duration_ms: float,
        stable: bool,
    ) -> CaseResult:
        selected = set(result.selected_context_ids)
        required = case.required_context_ids
        forbidden = case.forbidden_context_ids
        recall = len(selected & required) / len(required) if required else 1.0
        forbidden_rate = len(selected & forbidden) / len(forbidden) if forbidden else 0.0
        superseded_forbidden = {
            item.id
            for item in case.context
            if item.id in forbidden and item.status == ContextStatus.SUPERSEDED
        }
        superseded_rate = (
            len(selected & superseded_forbidden) / len(superseded_forbidden)
            if superseded_forbidden
            else 0.0
        )
        return CaseResult(
            case_id=case.id,
            strategy=result.strategy,
            selected_context_ids=result.selected_context_ids,
            estimated_tokens=result.estimated_tokens,
            duration_ms=duration_ms,
            required_recall=recall,
            forbidden_inclusion_rate=forbidden_rate,
            superseded_inclusion_rate=superseded_rate,
            selection_success=required <= selected and not (forbidden & selected),
            stable=stable,
        )

    @staticmethod
    def _summary(
        results: Sequence[CaseResult],
        input_price_per_million: float | None,
    ) -> StrategySummary:
        total_tokens = sum(result.estimated_tokens for result in results)
        cost = (
            total_tokens / 1_000_000 * input_price_per_million
            if input_price_per_million is not None
            else None
        )
        return StrategySummary(
            strategy=results[0].strategy,
            case_count=len(results),
            selection_success_rate=fmean(result.selection_success for result in results),
            mean_required_recall=fmean(result.required_recall for result in results),
            mean_forbidden_inclusion_rate=fmean(
                result.forbidden_inclusion_rate for result in results
            ),
            mean_superseded_inclusion_rate=fmean(
                result.superseded_inclusion_rate for result in results
            ),
            mean_estimated_tokens=fmean(result.estimated_tokens for result in results),
            mean_duration_ms=fmean(result.duration_ms for result in results),
            stability_rate=fmean(result.stable for result in results),
            estimated_input_cost=cost,
        )


def benchmark_dataset_hash(cases: Sequence[BenchmarkCase]) -> str:
    """Return the semantic dataset fingerprint shared by offline and model-backed runs."""

    records: list[str] = []
    for case in cases:
        payload = case.model_dump(mode="json")
        identity = payload.get("identity")
        if isinstance(identity, dict):
            for key in ("agent_id", "project_id"):
                if identity.get(key) is None:
                    identity.pop(key)
        for item in payload["context"]:
            if item.get("applicability") is None:
                item.pop("applicability")
            if item.get("source_message") is None:
                item.pop("source_message")
            owner = item.get("owner")
            if isinstance(owner, dict):
                for key in ("agent_id", "project_id"):
                    if owner.get(key) is None:
                        owner.pop(key)
        for key in (
            "required_context_ids",
            "forbidden_context_ids",
            "summary_context_ids",
        ):
            payload[key] = sorted(payload[key])
        records.append(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    canonical = "\n".join(records).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
