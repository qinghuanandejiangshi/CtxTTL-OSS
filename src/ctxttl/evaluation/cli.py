"""CLI for resumable, model-backed CtxTTL benchmark evaluation."""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from ctxttl.benchmarks.loaders import load_ctxttlbench
from ctxttl.benchmarks.models import BenchmarkCase, StrategyName
from ctxttl.benchmarks.runner import benchmark_dataset_hash
from ctxttl.benchmarks.strategies import default_strategies
from ctxttl.evaluation.checkpoint import EvaluationCheckpoint
from ctxttl.evaluation.comparison import compare_strategies
from ctxttl.evaluation.judge import LLMAnswerJudge
from ctxttl.evaluation.models import ModelEvaluationConfig, ModelEvaluationReport
from ctxttl.evaluation.openai_compatible import OpenAICompatibleInference
from ctxttl.evaluation.runner import ModelEvaluationRunner


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run model-backed CtxTTL evaluations.")
    parser.add_argument("dataset", type=Path)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--limit", type=int, default=None)
    selection.add_argument("--case-id-file", type=Path, default=None)
    parser.add_argument("--subject-model", default=os.getenv("CTXTTL_EVAL_MODEL"))
    parser.add_argument("--judge-model", default=os.getenv("CTXTTL_EVAL_JUDGE_MODEL"))
    parser.add_argument(
        "--base-url",
        default=os.getenv("CTXTTL_EVAL_BASE_URL", "https://api.openai.com/v1"),
    )
    parser.add_argument("--judge-base-url", default=os.getenv("CTXTTL_EVAL_JUDGE_BASE_URL"))
    parser.add_argument("--api-key-env", default="CTXTTL_EVAL_API_KEY")
    parser.add_argument("--judge-api-key-env", default="CTXTTL_EVAL_JUDGE_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument(
        "--target-context-tokens",
        type=int,
        default=None,
        help="override the dataset's normal input target for every selected case",
    )
    parser.add_argument(
        "--max-context-tokens",
        type=int,
        default=None,
        help="override the dataset's hard input ceiling for every selected case",
    )
    parser.add_argument("--samples-per-case", type=int, default=1)
    parser.add_argument("--subject-input-price", type=float, default=None)
    parser.add_argument("--subject-output-price", type=float, default=None)
    parser.add_argument("--judge-input-price", type=float, default=None)
    parser.add_argument("--judge-output-price", type=float, default=None)
    parser.add_argument("--subject-provider-options", type=_json_object, default={})
    parser.add_argument("--judge-provider-options", type=_json_object, default={})
    parser.add_argument(
        "--strategy",
        action="append",
        choices=tuple(strategy.value for strategy in StrategyName),
        default=None,
    )
    parser.add_argument(
        "--quality-baseline",
        choices=tuple(strategy.value for strategy in StrategyName),
        default=None,
        help="strategy whose answer accuracy the candidate must match within the allowed drop",
    )
    parser.add_argument(
        "--quality-candidate",
        choices=tuple(strategy.value for strategy in StrategyName),
        default=StrategyName.CTXTTL.value,
    )
    parser.add_argument("--max-accuracy-drop", type=float, default=0.0)
    parser.add_argument(
        "--quality-report",
        type=Path,
        default=None,
        help="write the paired baseline/candidate comparison as JSON",
    )
    parser.add_argument("--max-provider-calls", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    if args.max_provider_calls < 1:
        raise SystemExit("--max-provider-calls must be positive")
    if args.output.exists() and not args.overwrite:
        raise SystemExit(f"output already exists: {args.output}; use --overwrite")
    if args.quality_report is not None and args.quality_baseline is None:
        raise SystemExit("--quality-report requires --quality-baseline")
    if args.quality_report is not None and args.quality_report.exists() and not args.overwrite:
        raise SystemExit(f"quality report already exists: {args.quality_report}; use --overwrite")
    if not args.subject_model or not args.judge_model:
        raise SystemExit("--subject-model and --judge-model are required")
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise SystemExit(f"missing API key environment variable: {args.api_key_env}")
    judge_api_key = os.getenv(args.judge_api_key_env) or api_key
    cases = load_ctxttlbench(args.dataset, limit=args.limit)
    if args.case_id_file is not None:
        cases = _select_cases(cases, args.case_id_file)
    cases = _override_case_budgets(
        cases,
        target_tokens=args.target_context_tokens,
        max_tokens=args.max_context_tokens,
    )
    available = {strategy.name.value: strategy for strategy in default_strategies()}
    requested_names = args.strategy or list(available)
    if len(requested_names) != len(set(requested_names)):
        raise SystemExit("evaluation strategy names must be unique")
    strategies = tuple(available[name] for name in requested_names)
    if not 0.0 <= args.max_accuracy_drop <= 1.0:
        raise SystemExit("--max-accuracy-drop must be between 0 and 1")
    if args.quality_baseline is not None:
        if args.quality_baseline == args.quality_candidate:
            raise SystemExit("quality baseline and candidate must be different strategies")
        missing_quality_strategy = next(
            (
                name
                for name in (args.quality_baseline, args.quality_candidate)
                if name not in requested_names
            ),
            None,
        )
        if missing_quality_strategy is not None:
            raise SystemExit(f"quality gate strategy was not selected: {missing_quality_strategy}")
    config = ModelEvaluationConfig(
        subject_model=args.subject_model,
        judge_model=args.judge_model,
        temperature=args.temperature,
        seed=args.seed,
        max_output_tokens=args.max_output_tokens,
        samples_per_case=args.samples_per_case,
        subject_input_price_per_million_usd=args.subject_input_price,
        subject_output_price_per_million_usd=args.subject_output_price,
        judge_input_price_per_million_usd=args.judge_input_price,
        judge_output_price_per_million_usd=args.judge_output_price,
        subject_provider_options=args.subject_provider_options,
        judge_provider_options=args.judge_provider_options,
    )
    total_results = len(cases) * len(strategies) * config.samples_per_case
    total_calls = total_results * 2
    if total_calls > args.max_provider_calls and not args.resume:
        raise SystemExit(
            f"experiment requires {total_calls} provider calls, exceeding "
            f"--max-provider-calls={args.max_provider_calls}"
        )
    checkpoint_path = args.checkpoint or args.output.with_suffix(
        args.output.suffix + ".checkpoint.jsonl"
    )
    if checkpoint_path.resolve() == args.output.resolve():
        raise SystemExit("checkpoint and output paths must be different")
    checkpoint = EvaluationCheckpoint(
        checkpoint_path,
        dataset_sha256=benchmark_dataset_hash(cases),
        config=config,
        strategies=tuple(strategy.name for strategy in strategies),
        resume=args.resume,
    )
    remaining_results = total_results - len(checkpoint.results)
    remaining_calls = remaining_results * 2
    if remaining_results < 0:
        raise SystemExit("checkpoint contains more results than the requested experiment")
    if remaining_calls > args.max_provider_calls:
        raise SystemExit(
            f"experiment requires {remaining_calls} remaining provider calls, exceeding "
            f"--max-provider-calls={args.max_provider_calls}"
        )
    print(
        f"Running {remaining_results}/{total_results} remaining results "
        f"({remaining_calls} provider calls).",
        file=sys.stderr,
    )
    subject = OpenAICompatibleInference(args.base_url, api_key)
    judge_inference = OpenAICompatibleInference(
        args.judge_base_url or args.base_url,
        judge_api_key,
    )
    judge = LLMAnswerJudge(
        judge_inference,
        config.judge_model,
        provider_options=config.judge_provider_options,
    )
    try:
        report = await ModelEvaluationRunner(subject, judge, strategies).run(
            cases,
            config,
            existing_results=checkpoint.results,
            on_result=checkpoint.append,
        )
    finally:
        await subject.aclose()
        await judge_inference.aclose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(f"Wrote evaluation report: {args.output}", file=sys.stderr)
    if args.quality_baseline is not None:
        comparison = compare_strategies(
            report,
            baseline=StrategyName(args.quality_baseline),
            candidate=StrategyName(args.quality_candidate),
            noninferiority_margin=args.max_accuracy_drop,
        )
        if args.quality_report is not None:
            _write_json_atomically(args.quality_report, comparison.model_dump_json(indent=2))
        failure = _quality_gate_failure(
            report,
            baseline=StrategyName(args.quality_baseline),
            candidate=StrategyName(args.quality_candidate),
            max_accuracy_drop=args.max_accuracy_drop,
        )
        if failure is not None:
            print(failure, file=sys.stderr)
            return 2
    return 0


def _write_json_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content + "\n", encoding="utf-8")
    temporary.replace(path)


def _select_cases(cases: Sequence[BenchmarkCase], path: Path) -> list[BenchmarkCase]:
    try:
        identifiers = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except (OSError, UnicodeError) as error:
        raise SystemExit(f"unable to read case ID file {path}: {error}") from error
    if not identifiers:
        raise SystemExit(f"case ID file contains no IDs: {path}")
    if len(identifiers) != len(set(identifiers)):
        raise SystemExit("case ID file contains duplicate IDs")
    by_id = {case.id: case for case in cases}
    unknown = [identifier for identifier in identifiers if identifier not in by_id]
    if unknown:
        raise SystemExit(f"case ID file contains unknown ID: {unknown[0]}")
    return [by_id[identifier] for identifier in identifiers]


def _override_case_budgets(
    cases: Sequence[BenchmarkCase],
    *,
    target_tokens: int | None,
    max_tokens: int | None,
) -> list[BenchmarkCase]:
    if target_tokens is not None and target_tokens < 1:
        raise SystemExit("--target-context-tokens must be positive")
    if max_tokens is not None and max_tokens < 1:
        raise SystemExit("--max-context-tokens must be positive")
    overridden: list[BenchmarkCase] = []
    for case in cases:
        effective_target = target_tokens or case.target_tokens
        effective_max = max_tokens or case.max_tokens
        if effective_target > effective_max:
            raise SystemExit(
                "effective target context tokens cannot exceed effective max context tokens"
            )
        overridden.append(
            case.model_copy(
                update={
                    "target_tokens": effective_target,
                    "max_tokens": effective_max,
                }
            )
        )
    return overridden


def _quality_gate_failure(
    report: ModelEvaluationReport,
    *,
    baseline: StrategyName,
    candidate: StrategyName,
    max_accuracy_drop: float,
) -> str | None:
    summaries = {summary.strategy: summary for summary in report.summaries}
    baseline_accuracy = summaries[baseline].answer_accuracy
    candidate_accuracy = summaries[candidate].answer_accuracy
    minimum = baseline_accuracy - max_accuracy_drop
    if candidate_accuracy >= minimum:
        return None
    return (
        "quality gate failed: "
        f"{candidate.value} accuracy {candidate_accuracy:.4f} is below "
        f"{baseline.value} accuracy {baseline_accuracy:.4f} minus allowed drop "
        f"{max_accuracy_drop:.4f}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
