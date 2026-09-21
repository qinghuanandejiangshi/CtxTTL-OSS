"""Command-line entry point for offline benchmark replay."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from ctxttl.benchmarks.loaders import load_ctxttlbench
from ctxttl.benchmarks.runner import BenchmarkRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic CtxTTL context benchmarks.")
    parser.add_argument("dataset", type=Path, help="Path to a JSON or JSONL dataset")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--input-price-per-million", type=float, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    cases = load_ctxttlbench(args.dataset, limit=args.limit)
    report = BenchmarkRunner().run(
        cases,
        input_price_per_million=args.input_price_per_million,
    )
    rendered = report.model_dump_json(indent=2)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
