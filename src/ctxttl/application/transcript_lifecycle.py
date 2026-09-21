"""Provider-neutral transcript lifecycle filtering and accounting."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ctxttl.compiler.models import TokenEstimator
from ctxttl.compiler.tokenization import HeuristicTokenEstimator
from ctxttl.observability import PreCompilationExclusion


@dataclass(frozen=True, slots=True)
class InactiveTranscriptTurn:
    """An opaque turn marker declared inactive for a specific reason."""

    marker: str
    reason: str = "inactive"

    def __post_init__(self) -> None:
        if not self.marker:
            raise ValueError("turn marker must be non-empty")
        if not self.reason:
            raise ValueError("turn exclusion reason must be non-empty")


@dataclass(frozen=True, slots=True)
class LifecycleFilterResult:
    """Filtered provider payload and the exact messages removed from it."""

    payload: dict[str, Any]
    exclusion: PreCompilationExclusion


class TranscriptLifecycleFilter:
    """Remove complete user-led turns without understanding an Agent framework."""

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        self._estimator = estimator or HeuristicTokenEstimator()

    def apply(
        self,
        payload: Mapping[str, Any],
        inactive_turns: Sequence[InactiveTranscriptTurn],
    ) -> LifecycleFilterResult:
        output = dict(payload)
        messages = payload.get("messages", [])
        if not isinstance(messages, list) or not inactive_turns:
            return LifecycleFilterResult(output, PreCompilationExclusion())

        filtered: list[Any] = []
        message_counts: dict[str, int] = {}
        token_counts: dict[str, int] = {}
        active_reason: str | None = None
        for message in messages:
            if not isinstance(message, Mapping):
                filtered.append(message)
                active_reason = None
                continue
            role = message.get("role")
            if role in {"system", "developer"}:
                filtered.append(dict(message))
                active_reason = None
                continue
            if role == "user":
                content = message.get("content")
                active_reason = next(
                    (
                        turn.reason
                        for turn in inactive_turns
                        if isinstance(content, str) and turn.marker in content
                    ),
                    None,
                )
            if active_reason is None:
                filtered.append(dict(message))
            else:
                message_counts[active_reason] = message_counts.get(active_reason, 0) + 1
                token_counts[active_reason] = token_counts.get(active_reason, 0) + self._estimate(
                    message
                )

        output["messages"] = filtered
        return LifecycleFilterResult(
            output,
            PreCompilationExclusion(
                message_count=sum(message_counts.values()),
                token_count=sum(token_counts.values()),
                message_counts_by_reason=message_counts,
                tokens_by_reason=token_counts,
            ),
        )

    def _estimate(self, message: Mapping[str, Any]) -> int:
        value = self._estimator.estimate_message(message)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise RuntimeError("token estimator must return a positive integer")
        return value
