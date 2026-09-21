import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ctxttl.benchmarks.cli import main
from ctxttl.benchmarks.loaders import DatasetFormatError, load_ctxttlbench
from ctxttl.benchmarks.models import BenchmarkCase, BenchmarkContext, StrategyName
from ctxttl.benchmarks.runner import BenchmarkRunner
from ctxttl.benchmarks.strategies import CtxTTLStrategy, FullHistoryStrategy
from ctxttl.identity import RequestIdentity
from ctxttl.models import ContextStatus

DATASET = Path(__file__).parents[1] / "benchmarks" / "ctxttlbench.jsonl"


def test_bundled_dataset_runs_all_baselines_and_ctxttl() -> None:
    cases = load_ctxttlbench(DATASET)
    report = BenchmarkRunner().run(cases, input_price_per_million=2.0)
    summaries = {summary.strategy: summary for summary in report.summaries}

    assert len(cases) == 3
    assert set(summaries) == set(StrategyName)
    assert summaries[StrategyName.CTXTTL].selection_success_rate == 1.0
    assert summaries[StrategyName.FULL_HISTORY].mean_superseded_inclusion_rate > 0
    assert summaries[StrategyName.ACTIVE_HISTORY].mean_required_recall == 1
    assert summaries[StrategyName.CTXTTL].stability_rate == 1.0
    assert summaries[StrategyName.CTXTTL].estimated_input_cost is not None
    assert report.answer_accuracy is None


def test_natural_source_messages_hide_lifecycle_metadata_from_models() -> None:
    case = BenchmarkCase(
        id="natural-history",
        identity=RequestIdentity(session_id="session-1", task_id="task-1"),
        messages=({"role": "user", "content": "Which database should I use?"},),
        context=(
            BenchmarkContext(
                id="old",
                status=ContextStatus.RETRACTED,
                subject="database",
                value="MySQL",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                source_message={"role": "user", "content": "Let's use MySQL."},
            ),
            BenchmarkContext(
                id="current",
                subject="database",
                value="PostgreSQL",
                created_at=datetime(2026, 1, 2, tzinfo=UTC),
                source_message={"role": "user", "content": "Use PostgreSQL from now on."},
            ),
        ),
        required_context_ids=frozenset({"current"}),
        forbidden_context_ids=frozenset({"old"}),
        target_tokens=200,
        max_tokens=400,
    )

    full = FullHistoryStrategy().run(case)
    compiled = CtxTTLStrategy().run(case)

    assert full.selected_context_ids == ("old", "current")
    assert [message["content"] for message in full.messages] == [
        "Let's use MySQL.",
        "Use PostgreSQL from now on.",
        "Which database should I use?",
    ]
    assert all("retracted" not in message["content"] for message in full.messages)
    assert compiled.selected_context_ids == ("current",)
    assert [message["content"] for message in compiled.messages] == [
        "Use PostgreSQL from now on.",
        "Which database should I use?",
    ]


def test_dataset_hash_is_semantic_and_repeatable() -> None:
    cases = load_ctxttlbench(DATASET)

    first = BenchmarkRunner().run(cases)
    second = BenchmarkRunner().run(cases)

    assert first.dataset_sha256 == second.dataset_sha256
    assert first.dataset_sha256 == (
        "80de1dafca5d404a80ab58f7e9dbb634d4c8d5d95f18eafce1d628405a7b0681"
    )


def test_ctxttlbench_rejects_unknown_expected_ids(tmp_path: Path) -> None:
    invalid = {
        "id": "invalid",
        "identity": {"session_id": "session-1"},
        "messages": [],
        "required_context_ids": ["missing"],
    }
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")

    with pytest.raises(DatasetFormatError, match="must reference"):
        load_ctxttlbench(path)


def test_benchmark_cli_writes_machine_readable_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    exit_code = main([str(DATASET), "--limit", "1", "--output", str(output)])
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert report["dataset_case_count"] == 1
    assert len(report["summaries"]) == 6
