"""Replaceable token estimation policies."""

import json
import math
from collections.abc import Mapping
from typing import Any


class HeuristicTokenEstimator:
    """Dependency-free conservative estimator for model-agnostic MVP budgeting.

    Exact tokenizers remain provider adapters. The heuristic counts serialized UTF-8 bytes and
    adds protocol overhead, making the same input reproducible across supported Python versions.
    """

    _MESSAGE_OVERHEAD = 6

    @staticmethod
    def _content_tokens(value: Any) -> int:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return max(1, math.ceil(len(encoded) / 3))

    def estimate_message(self, message: Mapping[str, Any]) -> int:
        return self._MESSAGE_OVERHEAD + self._content_tokens(dict(message))
