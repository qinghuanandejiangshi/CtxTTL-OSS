"""Durable JSONL checkpoints for paid model evaluation runs."""

import json
import os
from pathlib import Path

from ctxttl.benchmarks.models import StrategyName
from ctxttl.evaluation.models import EvaluatedCase, ModelEvaluationConfig


class CheckpointError(RuntimeError):
    """Raised when a checkpoint is corrupt or belongs to another experiment."""


class EvaluationCheckpoint:
    """Append each completed subject-plus-judge result before the next paid call."""

    def __init__(
        self,
        path: Path,
        *,
        dataset_sha256: str,
        config: ModelEvaluationConfig,
        strategies: tuple[StrategyName, ...],
        resume: bool,
    ) -> None:
        self._path = path
        self._header = {
            "type": "header",
            "schema_version": 1,
            "dataset_sha256": dataset_sha256,
            "config": config.model_dump(mode="json"),
            "strategies": [strategy.value for strategy in strategies],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not resume:
                raise CheckpointError(f"checkpoint already exists: {path}; use --resume")
            self.results = self._load()
        else:
            if resume:
                raise CheckpointError(f"checkpoint does not exist: {path}")
            path.write_text(
                json.dumps(self._header, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.results: list[EvaluatedCase] = []

    def append(self, result: EvaluatedCase) -> None:
        record = {"type": "result", "result": result.model_dump(mode="json")}
        with self._path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.results.append(result)

    def _load(self) -> list[EvaluatedCase]:
        try:
            records = [
                json.loads(line)
                for line in self._path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointError(f"unable to read checkpoint: {self._path}") from error
        if not records or records[0] != self._header:
            raise CheckpointError("checkpoint metadata does not match the requested experiment")
        results: list[EvaluatedCase] = []
        keys: set[tuple[str, StrategyName, int]] = set()
        for position, record in enumerate(records[1:], start=2):
            if not isinstance(record, dict) or record.get("type") != "result":
                raise CheckpointError(f"invalid checkpoint record at line {position}")
            try:
                result = EvaluatedCase.model_validate(record.get("result"))
            except ValueError as error:
                raise CheckpointError(f"invalid checkpoint result at line {position}") from error
            key = (result.case_id, result.strategy, result.sample_index)
            if key in keys:
                raise CheckpointError(f"duplicate checkpoint result at line {position}")
            keys.add(key)
            results.append(result)
        return results
