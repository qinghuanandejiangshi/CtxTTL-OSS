"""Paired evaluation analysis for accuracy-first context experiments."""

from collections.abc import Sequence

from ctxttl.benchmarks.models import StrategyName
from ctxttl.evaluation.models import (
    EvaluatedCase,
    ModelEvaluationReport,
    PairedQualityComparison,
)


class ComparisonInputError(ValueError):
    """Raised when two strategy result sets cannot be paired safely."""


def compare_strategies(
    report: ModelEvaluationReport,
    *,
    baseline: StrategyName,
    candidate: StrategyName,
    noninferiority_margin: float = 0.0,
) -> PairedQualityComparison:
    """Compare matched case/sample outcomes and measured subject input usage."""

    if baseline == candidate:
        raise ComparisonInputError("baseline and candidate must be different strategies")
    if not 0 <= noninferiority_margin <= 1:
        raise ComparisonInputError("noninferiority margin must be between 0 and 1")

    baseline_results = _by_pair(report.cases, baseline)
    candidate_results = _by_pair(report.cases, candidate)
    if not baseline_results:
        raise ComparisonInputError(f"report has no results for baseline {baseline.value}")
    if not candidate_results:
        raise ComparisonInputError(f"report has no results for candidate {candidate.value}")
    if set(baseline_results) != set(candidate_results):
        raise ComparisonInputError("baseline and candidate case/sample keys do not match")

    pairs = [(baseline_results[key], candidate_results[key]) for key in sorted(baseline_results)]
    both_correct = sum(left.verdict.correct and right.verdict.correct for left, right in pairs)
    baseline_only = sum(left.verdict.correct and not right.verdict.correct for left, right in pairs)
    candidate_only = sum(
        not left.verdict.correct and right.verdict.correct for left, right in pairs
    )
    both_incorrect = len(pairs) - both_correct - baseline_only - candidate_only
    baseline_accuracy = (both_correct + baseline_only) / len(pairs)
    candidate_accuracy = (both_correct + candidate_only) / len(pairs)
    baseline_tokens = _complete_token_sum([left for left, _ in pairs])
    candidate_tokens = _complete_token_sum([right for _, right in pairs])
    reduction = (
        (baseline_tokens - candidate_tokens) / baseline_tokens
        if baseline_tokens not in (None, 0) and candidate_tokens is not None
        else None
    )
    difference = candidate_accuracy - baseline_accuracy
    return PairedQualityComparison(
        baseline=baseline,
        candidate=candidate,
        paired_sample_count=len(pairs),
        both_correct=both_correct,
        baseline_only_correct=baseline_only,
        candidate_only_correct=candidate_only,
        both_incorrect=both_incorrect,
        baseline_accuracy=baseline_accuracy,
        candidate_accuracy=candidate_accuracy,
        accuracy_difference=difference,
        noninferiority_margin=noninferiority_margin,
        observed_noninferiority_passed=difference >= -noninferiority_margin,
        baseline_prompt_tokens=baseline_tokens,
        candidate_prompt_tokens=candidate_tokens,
        prompt_token_reduction_rate=reduction,
    )


def _by_pair(
    results: Sequence[EvaluatedCase], strategy: StrategyName
) -> dict[tuple[str, int], EvaluatedCase]:
    selected: dict[tuple[str, int], EvaluatedCase] = {}
    for result in results:
        if result.strategy != strategy:
            continue
        key = (result.case_id, result.sample_index)
        if key in selected:
            raise ComparisonInputError(f"duplicate result for {strategy.value}: {key}")
        selected[key] = result
    return selected


def _complete_token_sum(results: Sequence[EvaluatedCase]) -> int | None:
    values = [result.subject_response.prompt_tokens for result in results]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)
