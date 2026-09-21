"""Dataset quality gate tests."""

from pathlib import Path

import pytest

from ctxttl.benchmarks import DatasetAuditError, audit_ctxttlbench, load_ctxttlbench

DATASET = Path(__file__).parents[1] / "benchmarks" / "ctxttlbench.jsonl"


def test_audit_reports_exact_provider_call_envelope() -> None:
    cases = load_ctxttlbench(DATASET)
    enriched = [
        case.model_copy(update={"metadata": {**case.metadata, "answer_type": "short_text"}})
        for case in cases
    ]

    report = audit_ctxttlbench(enriched, samples_per_case=3, min_cases=3, min_scenarios=3)

    assert report.case_count == 3
    assert report.estimated_subject_calls == 54
    assert report.estimated_judge_calls == 54
    assert report.estimated_provider_calls == 108


def test_audit_rejects_missing_answer_type() -> None:
    cases = load_ctxttlbench(DATASET)

    with pytest.raises(DatasetAuditError, match="metadata.answer_type"):
        audit_ctxttlbench(cases)


def test_audit_rejects_unreachable_required_context() -> None:
    case = load_ctxttlbench(DATASET, limit=1)[0]
    unreachable_context = case.context[1].model_copy(update={"task_id": "another-task"})
    invalid = case.model_copy(
        update={
            "context": (case.context[0], unreachable_context),
            "metadata": {**case.metadata, "answer_type": "short_text"},
        }
    )

    with pytest.raises(DatasetAuditError, match="not identity-reachable"):
        audit_ctxttlbench([invalid])
