"""Safety checks for the paid model evaluation CLI."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ctxttl.benchmarks.loaders import load_ctxttlbench
from ctxttl.benchmarks.models import StrategyName
from ctxttl.benchmarks.runner import benchmark_dataset_hash
from ctxttl.evaluation.cli import (
    _override_case_budgets,
    _quality_gate_failure,
    _select_cases,
    main,
)
from ctxttl.evaluation.models import ModelEvaluationReport, ModelEvaluationSummary


class CompletionHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        type(self).calls += 1
        length = int(self.headers["content-length"])
        payload = json.loads(self.rfile.read(length))
        if payload.get("response_format"):
            content = json.dumps(
                {
                    "score": 1,
                    "correct": True,
                    "confidence": 1,
                    "rationale": "matches",
                }
            )
        else:
            content = "PostgreSQL"
        body = json.dumps(
            {
                "id": f"response-{type(self).calls}",
                "model": payload["model"],
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return None


def test_cli_rejects_call_budget_before_creating_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CTXTTL_EVAL_API_KEY", "test-key")
    dataset = Path(__file__).parents[1] / "benchmarks" / "ctxttlbench.jsonl"
    output = tmp_path / "report.json"

    with pytest.raises(SystemExit, match="requires 12 provider calls"):
        main(
            [
                str(dataset),
                "--limit",
                "1",
                "--subject-model",
                "subject",
                "--judge-model",
                "judge",
                "--max-provider-calls",
                "1",
                "--output",
                str(output),
            ]
        )

    assert not output.exists()
    assert not output.with_suffix(".json.checkpoint.jsonl").exists()


def test_cli_runs_end_to_end_against_chat_completions_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CTXTTL_EVAL_API_KEY", "test-key")
    dataset = Path(__file__).parents[1] / "benchmarks" / "ctxttlbench.jsonl"
    output = tmp_path / "report.json"
    quality_output = tmp_path / "quality.json"
    CompletionHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), CompletionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = main(
            [
                str(dataset),
                "--limit",
                "1",
                "--strategy",
                "full_history",
                "--strategy",
                "ctxttl",
                "--quality-baseline",
                "full_history",
                "--quality-candidate",
                "ctxttl",
                "--quality-report",
                str(quality_output),
                "--subject-model",
                "subject",
                "--judge-model",
                "judge",
                "--base-url",
                f"http://127.0.0.1:{server.server_port}/v1",
                "--max-provider-calls",
                "4",
                "--output",
                str(output),
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    report = json.loads(output.read_text(encoding="utf-8"))
    comparison = json.loads(quality_output.read_text(encoding="utf-8"))
    assert result == 0
    assert CompletionHandler.calls == 4
    assert report["summaries"][0]["answer_accuracy"] == 1
    assert comparison["paired_sample_count"] == 1
    assert comparison["observed_noninferiority_passed"] is True


def test_case_id_file_selects_declared_order(tmp_path: Path) -> None:
    dataset = Path(__file__).parents[1] / "benchmarks" / "ctxttlbench.jsonl"
    cases = load_ctxttlbench(dataset)
    manifest = tmp_path / "cases.txt"
    manifest.write_text(
        "# frozen subset\n" + cases[2].id + "\n" + cases[0].id + "\n",
        encoding="utf-8",
    )

    selected = _select_cases(cases, manifest)

    assert [case.id for case in selected] == [cases[2].id, cases[0].id]


def test_context_budget_override_is_explicit() -> None:
    dataset = Path(__file__).parents[1] / "benchmarks" / "ctxttlbench.jsonl"
    original = load_ctxttlbench(dataset, limit=1)

    overridden = _override_case_budgets(original, target_tokens=24_000, max_tokens=64_000)

    assert overridden[0].target_tokens == 24_000
    assert overridden[0].max_tokens == 64_000
    assert original[0].target_tokens != overridden[0].target_tokens
    assert benchmark_dataset_hash(original) != benchmark_dataset_hash(overridden)


def test_context_budget_override_rejects_invalid_effective_window() -> None:
    dataset = Path(__file__).parents[1] / "benchmarks" / "ctxttlbench.jsonl"
    cases = load_ctxttlbench(dataset, limit=1)

    with pytest.raises(SystemExit, match="cannot exceed"):
        _override_case_budgets(cases, target_tokens=64_000, max_tokens=24_000)


def test_quality_gate_rejects_accuracy_regression() -> None:
    def summary(strategy: StrategyName, accuracy: float) -> ModelEvaluationSummary:
        return ModelEvaluationSummary(
            strategy=strategy,
            sample_count=10,
            mean_judge_score=accuracy,
            answer_accuracy=accuracy,
            normalized_exact_match_accuracy=0.0,
            normalized_reference_presence=0.0,
            mean_subject_latency_ms=1.0,
            mean_judge_latency_ms=1.0,
        )

    report = ModelEvaluationReport.model_construct(
        summaries=(
            summary(StrategyName.FULL_HISTORY, 0.9),
            summary(StrategyName.CTXTTL, 0.8),
        )
    )

    failure = _quality_gate_failure(
        report,
        baseline=StrategyName.FULL_HISTORY,
        candidate=StrategyName.CTXTTL,
        max_accuracy_drop=0.0,
    )

    assert failure is not None
    assert "quality gate failed" in failure


def test_quality_gate_accepts_non_inferior_candidate() -> None:
    summaries = tuple(
        ModelEvaluationSummary.model_construct(strategy=strategy, answer_accuracy=accuracy)
        for strategy, accuracy in (
            (StrategyName.FULL_HISTORY, 0.8),
            (StrategyName.CTXTTL, 0.8),
        )
    )
    report = ModelEvaluationReport.model_construct(summaries=summaries)

    assert (
        _quality_gate_failure(
            report,
            baseline=StrategyName.FULL_HISTORY,
            candidate=StrategyName.CTXTTL,
            max_accuracy_drop=0.0,
        )
        is None
    )
