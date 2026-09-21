"""Public contracts for deterministic context compilation."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from ctxttl.models import ContextItem


class CompilationError(ValueError):
    """Base class for safe, user-visible compilation failures."""


class ProtocolViolation(CompilationError):
    """Raised when input cannot be transformed without breaking its protocol."""


class TokenBudgetExceeded(CompilationError):
    """Raised when mandatory input alone exceeds the configured safety ceiling."""


class DecisionReason(StrEnum):
    HARD_REQUIRED = "hard_required"
    SOFT_SELECTED = "soft_selected"
    INACTIVE = "inactive"
    REDUNDANT = "redundant"
    LOW_RELEVANCE = "low_relevance"
    BUDGET_EXCEEDED = "budget_exceeded"


class TokenEstimator(Protocol):
    """Replaceable token cost estimator."""

    def estimate_message(self, message: Mapping[str, Any]) -> int: ...


class ContextRelevanceScorer(Protocol):
    """Replaceable query-to-context relevance scorer returning a value in [0, 1]."""

    def score(self, query: str, item: ContextItem) -> float: ...


class ContextMessageRenderer(Protocol):
    """Replaceable presentation boundary for one selected context item."""

    def render(self, item: ContextItem) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CompilationDecision:
    """One replayable selection decision."""

    candidate_id: str
    candidate_type: str
    included: bool
    reason: DecisionReason
    token_cost: int
    utility: float | None


@dataclass(frozen=True, slots=True)
class CompilationResult:
    """Compiled messages plus the decisions needed to explain the result."""

    messages: tuple[dict[str, Any], ...]
    selected_context_ids: tuple[str, ...]
    source_message_tokens: int
    estimated_tokens: int
    target_tokens: int
    max_tokens: int
    decisions: tuple[CompilationDecision, ...]
