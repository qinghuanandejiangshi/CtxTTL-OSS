"""Generic hard-constraint and soft-budget selection."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ctxttl.compiler.models import CompilationDecision, DecisionReason, TokenBudgetExceeded


@dataclass(frozen=True, slots=True)
class BudgetCandidate:
    """A protocol-independent candidate considered by the budget optimizer."""

    id: str
    candidate_type: str
    token_cost: int
    hard: bool
    utility: float
    tie_breaker: tuple[int, str, str]
    payload: object

    def __post_init__(self) -> None:
        if isinstance(self.token_cost, bool) or not isinstance(self.token_cost, int):
            raise TypeError("candidate token_cost must be an integer")
        if self.token_cost < 1:
            raise ValueError("candidate token_cost must be positive")
        if not math.isfinite(self.utility):
            raise ValueError("candidate utility must be finite")


def select_candidates(
    candidates: Sequence[BudgetCandidate],
    *,
    target_tokens: int,
    max_tokens: int,
) -> tuple[list[BudgetCandidate], list[CompilationDecision]]:
    """Select mandatory candidates, then admit soft candidates by deterministic utility."""

    if target_tokens < 1 or max_tokens < target_tokens:
        raise ValueError("token budgets must satisfy 1 <= target_tokens <= max_tokens")

    hard = [candidate for candidate in candidates if candidate.hard]
    soft = [candidate for candidate in candidates if not candidate.hard]
    hard_cost = sum(candidate.token_cost for candidate in hard)
    if hard_cost > max_tokens:
        raise TokenBudgetExceeded(
            "mandatory messages and context exceed max_context_tokens; increase the limit or "
            "reduce mandatory input"
        )

    selected = list(hard)
    decisions = [
        CompilationDecision(
            candidate.id,
            candidate.candidate_type,
            True,
            DecisionReason.HARD_REQUIRED,
            candidate.token_cost,
            candidate.utility,
        )
        for candidate in hard
    ]
    used = hard_cost
    soft_limit = max(target_tokens, hard_cost)
    for candidate in sorted(
        soft,
        key=lambda value: (value.utility, value.tie_breaker),
        reverse=True,
    ):
        included = used + candidate.token_cost <= soft_limit
        if included:
            selected.append(candidate)
            used += candidate.token_cost
        decisions.append(
            CompilationDecision(
                candidate.id,
                candidate.candidate_type,
                included,
                DecisionReason.SOFT_SELECTED if included else DecisionReason.BUDGET_EXCEEDED,
                candidate.token_cost,
                candidate.utility,
            )
        )
    return selected, decisions
