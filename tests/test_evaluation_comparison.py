"""Paired quality comparison tests."""

from datetime import UTC, datetime

import pytest

from ctxttl.benchmarks.models import StrategyName
from ctxttl.evaluation.comparison import ComparisonInputError, compare_strategies
from ctxttl.evaluation.models import (
    ChatInferenceResponse,
    EvaluatedCase,
    JudgeVerdict,
    ModelEvaluationConfig,
    ModelEvaluationReport,
)


def _result(
    case_id: str,
    strategy: StrategyName,
    *,
    correct: bool,
    prompt_tokens: int | None,
) -> EvaluatedCase:
    return EvaluatedCase(
        case_id=case_id,
        strategy=strategy,
        sample_index=0,
        selected_context_ids=(),
        estimated_input_tokens=1,
        answer="answer",
        normalized_exact_match=correct,
        normalized_reference_present=correct,
        subject_response=ChatInferenceResponse(
            content="answer",
            latency_ms=1,
            prompt_tokens=prompt_tokens,
        ),
        verdict=JudgeVerdict(
            score=float(correct),
            correct=correct,
            confidence=1,
            rationale="test",
            latency_ms=1,
        ),
    )


def _report(cases: tuple[EvaluatedCase, ...]) -> ModelEvaluationReport:
    return ModelEvaluationReport(
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        dataset_sha256="0" * 64,
        dataset_case_count=2,
        config=ModelEvaluationConfig(subject_model="subject", judge_model="judge"),
        summaries=(),
        cases=cases,
    )


def test_compare_strategies_aligns_outcomes_and_measured_tokens() -> None:
    comparison = compare_strategies(
        _report(
            (
                _result("a", StrategyName.ACTIVE_HISTORY, correct=True, prompt_tokens=100),
                _result("a", StrategyName.CTXTTL, correct=True, prompt_tokens=20),
                _result("b", StrategyName.ACTIVE_HISTORY, correct=True, prompt_tokens=100),
                _result("b", StrategyName.CTXTTL, correct=False, prompt_tokens=20),
            )
        ),
        baseline=StrategyName.ACTIVE_HISTORY,
        candidate=StrategyName.CTXTTL,
        noninferiority_margin=0.5,
    )

    assert comparison.paired_sample_count == 2
    assert comparison.both_correct == 1
    assert comparison.baseline_only_correct == 1
    assert comparison.candidate_only_correct == 0
    assert comparison.accuracy_difference == -0.5
    assert comparison.observed_noninferiority_passed is True
    assert comparison.prompt_token_reduction_rate == pytest.approx(0.8)


def test_compare_strategies_rejects_unmatched_samples() -> None:
    report = _report(
        (
            _result("a", StrategyName.ACTIVE_HISTORY, correct=True, prompt_tokens=10),
            _result("b", StrategyName.CTXTTL, correct=True, prompt_tokens=10),
        )
    )

    with pytest.raises(ComparisonInputError, match="do not match"):
        compare_strategies(
            report,
            baseline=StrategyName.ACTIVE_HISTORY,
            candidate=StrategyName.CTXTTL,
        )


def test_compare_strategies_preserves_unknown_usage() -> None:
    comparison = compare_strategies(
        _report(
            (
                _result("a", StrategyName.ACTIVE_HISTORY, correct=True, prompt_tokens=None),
                _result("a", StrategyName.CTXTTL, correct=True, prompt_tokens=10),
            )
        ),
        baseline=StrategyName.ACTIVE_HISTORY,
        candidate=StrategyName.CTXTTL,
    )

    assert comparison.baseline_prompt_tokens is None
    assert comparison.prompt_token_reduction_rate is None
