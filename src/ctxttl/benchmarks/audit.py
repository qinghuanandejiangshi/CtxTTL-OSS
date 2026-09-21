"""Preflight quality gates for publishable CtxTTLBench datasets."""

from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ctxttl.benchmarks.models import BenchmarkCase, StrategyName
from ctxttl.benchmarks.runner import benchmark_dataset_hash
from ctxttl.identity import reachable_owner_keys
from ctxttl.models import ContextStatus


class DatasetAuditError(ValueError):
    """Raised when a dataset cannot safely enter paid model evaluation."""


class DatasetAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1)
    scenario_counts: dict[str, int]
    answer_type_counts: dict[str, int]
    strategy_count: int = Field(ge=1)
    samples_per_case: int = Field(ge=1)
    estimated_subject_calls: int = Field(ge=1)
    estimated_judge_calls: int = Field(ge=1)
    estimated_provider_calls: int = Field(ge=2)


def audit_ctxttlbench(
    cases: Sequence[BenchmarkCase],
    *,
    samples_per_case: int = 1,
    min_cases: int = 1,
    min_scenarios: int = 1,
    strategy_count: int = len(StrategyName),
) -> DatasetAuditReport:
    """Validate evaluation semantics and calculate the exact paid-call envelope."""

    if not cases:
        raise DatasetAuditError("dataset contains no cases")
    for name, value in (
        ("samples_per_case", samples_per_case),
        ("min_cases", min_cases),
        ("min_scenarios", min_scenarios),
        ("strategy_count", strategy_count),
    ):
        if isinstance(value, bool) or value < 1:
            raise DatasetAuditError(f"{name} must be a positive integer")
    identifiers = [case.id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise DatasetAuditError("dataset case IDs must be unique")
    if len(cases) < min_cases:
        raise DatasetAuditError(f"dataset requires at least {min_cases} cases")

    scenario_counts: Counter[str] = Counter()
    answer_type_counts: Counter[str] = Counter()
    for case in cases:
        scenario = _metadata_label(case, "scenario")
        answer_type = _metadata_label(case, "answer_type")
        scenario_counts[scenario] += 1
        answer_type_counts[answer_type] += 1
        gold_answer = case.metadata.get("gold_answer")
        if gold_answer is None or (isinstance(gold_answer, str) and not gold_answer.strip()):
            raise DatasetAuditError(f"case {case.id} requires a non-empty gold_answer")
        if not case.required_context_ids:
            raise DatasetAuditError(f"case {case.id} requires at least one required context ID")
        contexts = {item.id: item.to_item(case.identity) for item in case.context}
        reachable = set(reachable_owner_keys(case.identity))
        for context_id in case.required_context_ids:
            item = contexts[context_id]
            if item.status != ContextStatus.ACTIVE:
                raise DatasetAuditError(
                    f"case {case.id} required context {context_id} must be active"
                )
            if item.owner_key not in reachable:
                raise DatasetAuditError(
                    f"case {case.id} required context {context_id} is not identity-reachable"
                )
    if len(scenario_counts) < min_scenarios:
        raise DatasetAuditError(f"dataset requires at least {min_scenarios} scenarios")

    subject_calls = len(cases) * strategy_count * samples_per_case
    return DatasetAuditReport(
        dataset_sha256=benchmark_dataset_hash(cases),
        case_count=len(cases),
        scenario_counts=dict(sorted(scenario_counts.items())),
        answer_type_counts=dict(sorted(answer_type_counts.items())),
        strategy_count=strategy_count,
        samples_per_case=samples_per_case,
        estimated_subject_calls=subject_calls,
        estimated_judge_calls=subject_calls,
        estimated_provider_calls=subject_calls * 2,
    )


def _metadata_label(case: BenchmarkCase, name: str) -> str:
    value = case.metadata.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DatasetAuditError(f"case {case.id} requires metadata.{name}")
    return value.strip()
