"""Redacted structured logging for the MCP boundary."""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

_STANDARD_ATTRIBUTES = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}


def opaque_identity(value: str) -> str:
    """Return a stable log-safe identity fingerprint."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class JSONFormatter(logging.Formatter):
    """Serialize logs without accidentally echoing context values or credentials."""

    def format(self, record: logging.LogRecord) -> str:
        output: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES and not key.startswith("_"):
                output[key] = value
        if record.exc_info:
            output["exception_type"] = record.exc_info[0].__name__
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(level: str) -> None:
    """Configure one JSON stream for the standalone process."""

    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
