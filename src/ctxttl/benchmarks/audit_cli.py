"""Command-line dataset quality gate and paid-call estimator."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from ctxttl.benchmarks.audit import audit_ctxttlbench
from ctxttl.benchmarks.loaders import load_ctxttlbench


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit a CtxTTLBench dataset before model calls.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--samples-per-case", type=int, default=1)
    parser.add_argument("--min-cases", type=int, default=1)
    parser.add_argument("--min-scenarios", type=int, default=1)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = load_ctxttlbench(args.dataset)
    report = audit_ctxttlbench(
        cases,
        samples_per_case=args.samples_per_case,
        min_cases=args.min_cases,
        min_scenarios=args.min_scenarios,
    )
    rendered = report.model_dump_json(indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
