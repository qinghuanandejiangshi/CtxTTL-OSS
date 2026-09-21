"""Durable evaluation checkpoint tests."""

from pathlib import Path

import pytest

from ctxttl.benchmarks.models import StrategyName
from ctxttl.evaluation import (
    ChatInferenceResponse,
    CheckpointError,
    EvaluatedCase,
    EvaluationCheckpoint,
    JudgeVerdict,
    ModelEvaluationConfig,
)


def evaluated_case() -> EvaluatedCase:
    return EvaluatedCase(
        case_id="case-1",
        strategy=StrategyName.FULL_HISTORY,
        sample_index=0,
        selected_context_ids=("context-1",),
        estimated_input_tokens=10,
        answer="PostgreSQL",
        normalized_exact_match=True,
        normalized_reference_present=True,
        subject_response=ChatInferenceResponse(
            content="PostgreSQL",
            model="subject",
            prompt_tokens=10,
            completion_tokens=2,
            latency_ms=5,
        ),
        verdict=JudgeVerdict(
            score=1,
            correct=True,
            confidence=1,
            rationale="matches",
            judge_model="judge",
            latency_ms=3,
        ),
    )


def checkpoint(path: Path, *, resume: bool) -> EvaluationCheckpoint:
    return EvaluationCheckpoint(
        path,
        dataset_sha256="a" * 64,
        config=ModelEvaluationConfig(subject_model="subject", judge_model="judge"),
        strategies=(StrategyName.FULL_HISTORY,),
        resume=resume,
    )


def test_checkpoint_round_trip_supports_resume(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.jsonl"
    created = checkpoint(path, resume=False)
    created.append(evaluated_case())

    resumed = checkpoint(path, resume=True)

    assert resumed.results == [evaluated_case()]


def test_checkpoint_rejects_existing_file_without_resume(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.jsonl"
    checkpoint(path, resume=False)

    with pytest.raises(CheckpointError, match="already exists"):
        checkpoint(path, resume=False)


def test_checkpoint_rejects_another_experiment(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.jsonl"
    checkpoint(path, resume=False)

    with pytest.raises(CheckpointError, match="does not match"):
        EvaluationCheckpoint(
            path,
            dataset_sha256="b" * 64,
            config=ModelEvaluationConfig(subject_model="subject", judge_model="judge"),
            strategies=(StrategyName.FULL_HISTORY,),
            resume=True,
        )
