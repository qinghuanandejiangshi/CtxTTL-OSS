"""Append-only, hash-chained evidence ledger for reproducible experiments."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any


class ExperimentLedgerError(RuntimeError):
    """Raised when an experiment ledger cannot be safely created or resumed."""


_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "access_token",
    "refresh_token",
    "secret",
}


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            output[str(key)] = (
                "[REDACTED]"
                if normalized in _SENSITIVE_KEYS or normalized.endswith("_api_key")
                else _redact(child)
            )
        return output
    if isinstance(value, (list, tuple)):
        return [_redact(child) for child in value]
    return value


def _canonical(record: Mapping[str, Any]) -> bytes:
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class ExperimentLedger:
    """Durably append evidence while making truncation or modification detectable."""

    def __init__(
        self,
        path: Path,
        *,
        experiment: str,
        manifest: Mapping[str, Any],
        resume: bool = False,
    ) -> None:
        if not experiment.strip():
            raise ValueError("experiment must be non-empty")
        self._path = path
        self._lock = RLock()
        self._records: list[dict[str, Any]] = []
        self._header_payload = {
            "experiment": experiment,
            "manifest": _redact(dict(manifest)),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not resume:
                raise ExperimentLedgerError(f"ledger already exists: {path}; use resume=True")
            self._records = self._load()
            first = self._records[0]
            if first.get("payload") != self._header_payload:
                raise ExperimentLedgerError("ledger manifest does not match requested experiment")
        else:
            if resume:
                raise ExperimentLedgerError(f"ledger does not exist: {path}")
            self._append("header", self._header_payload)

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def tail_sha256(self) -> str:
        return str(self._records[-1]["record_sha256"])

    def append(self, event_type: str, payload: Mapping[str, Any]) -> str:
        """Append one redacted event, flush it, and return its record hash."""

        if not event_type.strip() or event_type == "header":
            raise ValueError("event_type must be non-empty and cannot be header")
        with self._lock:
            return self._append(event_type, _redact(dict(payload)))

    def _append(self, event_type: str, payload: Mapping[str, Any]) -> str:
        previous = self._records[-1]["record_sha256"] if self._records else None
        unsigned = {
            "schema_version": 1,
            "sequence": len(self._records),
            "recorded_at": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "previous_sha256": previous,
            "payload": payload,
        }
        digest = hashlib.sha256(_canonical(unsigned)).hexdigest()
        record = {**unsigned, "record_sha256": digest}
        with self._path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._records.append(record)
        return digest

    def _load(self) -> list[dict[str, Any]]:
        try:
            lines = [line for line in self._path.read_text(encoding="utf-8").splitlines() if line]
            records = [json.loads(line) for line in lines]
        except (OSError, json.JSONDecodeError) as error:
            raise ExperimentLedgerError(f"unable to read ledger: {self._path}") from error
        if not records:
            raise ExperimentLedgerError("ledger is empty")
        previous: str | None = None
        for sequence, record in enumerate(records):
            if not isinstance(record, dict):
                raise ExperimentLedgerError(f"invalid record at line {sequence + 1}")
            digest = record.get("record_sha256")
            unsigned = {key: value for key, value in record.items() if key != "record_sha256"}
            expected = hashlib.sha256(_canonical(unsigned)).hexdigest()
            if (
                record.get("schema_version") != 1
                or record.get("sequence") != sequence
                or record.get("previous_sha256") != previous
                or digest != expected
            ):
                raise ExperimentLedgerError(f"ledger chain is invalid at line {sequence + 1}")
            previous = digest
        if records[0].get("event_type") != "header":
            raise ExperimentLedgerError("first ledger record must be a header")
        return records
