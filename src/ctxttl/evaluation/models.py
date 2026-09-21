"""Provider-neutral contracts for model-backed benchmark evaluation."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ctxttl.benchmarks.models import StrategyName

DEFAULT_SUBJECT_INSTRUCTION = (
    "Answer the final user question directly and concisely using only the supplied messages and "
    "context. State the answer first. Do not invent missing task details, provide implementation "
    "steps, or ask follow-up questions."
)


class ChatInferenceRequest(BaseModel):
    """One frozen model invocation with explicit reproducibility settings."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    model: str = Field(min_length=1)
    messages: tuple[dict[str, Any], ...]
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int | None = None
    max_output_tokens: int = Field(default=512, ge=1)
    response_format: dict[str, JsonValue] | None = None
    provider_options: dict[str, JsonValue] = Field(default_factory=dict)


class ChatInferenceResponse(BaseModel):
    """Normalized output and measured usage from an inference provider."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    content: str
    model: str | None = None
    response_id: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)


class JudgeRequest(BaseModel):
    """Inputs required to evaluate one generated answer against declared evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reference_answer: JsonValue
    candidate_answer: str


class JudgeVerdict(BaseModel):
    """Normalized judge decision retained for later audit."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    score: float = Field(ge=0.0, le=1.0)
    correct: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    judge_model: str | None = None
    latency_ms: float = Field(ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    judge_prompt_version: str = Field(default="answer-correctness-v1", min_length=1)


class ModelEvaluationConfig(BaseModel):
    """Frozen inference settings recorded alongside every experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    subject_model: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int | None = 0
    max_output_tokens: int = Field(default=512, ge=1)
    samples_per_case: int = Field(default=1, ge=1, le=100)
    subject_input_price_per_million_usd: float | None = Field(default=None, ge=0)
    subject_output_price_per_million_usd: float | None = Field(default=None, ge=0)
    judge_input_price_per_million_usd: float | None = Field(default=None, ge=0)
    judge_output_price_per_million_usd: float | None = Field(default=None, ge=0)
    subject_provider_options: dict[str, JsonValue] = Field(default_factory=dict)
    judge_provider_options: dict[str, JsonValue] = Field(default_factory=dict)
    subject_instruction: str = Field(default=DEFAULT_SUBJECT_INSTRUCTION, min_length=1)


class EvaluatedCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    strategy: StrategyName
    sample_index: int = Field(ge=0)
    selected_context_ids: tuple[str, ...]
    estimated_input_tokens: int = Field(ge=0)
    answer: str
    normalized_exact_match: bool
    normalized_reference_present: bool
    subject_response: ChatInferenceResponse
    verdict: JudgeVerdict


class ModelEvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    strategy: StrategyName
    sample_count: int = Field(ge=1)
    mean_judge_score: float = Field(ge=0.0, le=1.0)
    answer_accuracy: float = Field(ge=0.0, le=1.0)
    normalized_exact_match_accuracy: float = Field(ge=0.0, le=1.0)
    normalized_reference_presence: float = Field(ge=0.0, le=1.0)
    mean_subject_latency_ms: float = Field(ge=0)
    mean_judge_latency_ms: float = Field(ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    judge_prompt_tokens: int | None = Field(default=None, ge=0)
    judge_completion_tokens: int | None = Field(default=None, ge=0)
    estimated_subject_cost_usd: float | None = Field(default=None, ge=0)
    estimated_judge_cost_usd: float | None = Field(default=None, ge=0)
    estimated_total_cost_usd: float | None = Field(default=None, ge=0)


class ModelEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_case_count: int = Field(ge=1)
    config: ModelEvaluationConfig
    summaries: tuple[ModelEvaluationSummary, ...]
    cases: tuple[EvaluatedCase, ...]


class PairedQualityComparison(BaseModel):
    """Task-aligned quality and token comparison between two context strategies."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: int = 1
    baseline: StrategyName
    candidate: StrategyName
    paired_sample_count: int = Field(ge=1)
    both_correct: int = Field(ge=0)
    baseline_only_correct: int = Field(ge=0)
    candidate_only_correct: int = Field(ge=0)
    both_incorrect: int = Field(ge=0)
    baseline_accuracy: float = Field(ge=0, le=1)
    candidate_accuracy: float = Field(ge=0, le=1)
    accuracy_difference: float = Field(ge=-1, le=1)
    noninferiority_margin: float = Field(ge=0, le=1)
    observed_noninferiority_passed: bool
    baseline_prompt_tokens: int | None = Field(default=None, ge=0)
    candidate_prompt_tokens: int | None = Field(default=None, ge=0)
    prompt_token_reduction_rate: float | None = Field(default=None, le=1)

    @model_validator(mode="after")
    def validate_pair_counts(self) -> "PairedQualityComparison":
        total = (
            self.both_correct
            + self.baseline_only_correct
            + self.candidate_only_correct
            + self.both_incorrect
        )
        if total != self.paired_sample_count:
            raise ValueError("paired outcome counts must equal paired_sample_count")
        return self
