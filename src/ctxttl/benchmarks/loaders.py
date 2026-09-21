"""Dataset loader for the public CtxTTLBench contract fixture."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ctxttl.benchmarks.models import BenchmarkCase


class DatasetFormatError(ValueError):
    """Raised when a benchmark dataset cannot be interpreted safely."""


def _json_records(path: Path) -> Iterator[tuple[int, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.read(4_096).lstrip()[:1]
            handle.seek(0)
            if first == "[":
                payload = json.load(handle)
                if not isinstance(payload, list):
                    raise DatasetFormatError("JSON dataset root must be an array")
                yield from enumerate(payload, start=1)
                return
            for line_number, line in enumerate(handle, start=1):
                if line.strip():
                    yield line_number, json.loads(line)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatasetFormatError(f"unable to read dataset {path}: {error}") from error


def load_ctxttlbench(path: str | Path, *, limit: int | None = None) -> list[BenchmarkCase]:
    """Load strict CtxTTLBench JSON or JSONL records."""

    _validate_limit(limit)
    cases: list[BenchmarkCase] = []
    for position, payload in _json_records(Path(path)):
        try:
            cases.append(BenchmarkCase.model_validate(payload))
        except ValueError as error:
            raise DatasetFormatError(f"invalid CtxTTLBench record {position}: {error}") from error
        if limit is not None and len(cases) >= limit:
            break
    if not cases:
        raise DatasetFormatError("dataset contains no cases")
    return cases


def _validate_limit(limit: int | None) -> None:
    if isinstance(limit, bool) or (limit is not None and limit < 1):
        raise ValueError("dataset limit must be positive")
