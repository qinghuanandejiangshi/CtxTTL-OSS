"""Model-backed evaluation contracts and adapters."""

from ctxttl.evaluation.checkpoint import CheckpointError, EvaluationCheckpoint
from ctxttl.evaluation.comparison import ComparisonInputError, compare_strategies
from ctxttl.evaluation.judge import JudgeError, LLMAnswerJudge
from ctxttl.evaluation.metrics import (
    normalize_answer,
    normalized_exact_match,
    normalized_reference_present,
)
from ctxttl.evaluation.models import (
    ChatInferenceRequest,
    ChatInferenceResponse,
    EvaluatedCase,
    JudgeRequest,
    JudgeVerdict,
    ModelEvaluationConfig,
    ModelEvaluationReport,
    ModelEvaluationSummary,
    PairedQualityComparison,
)
from ctxttl.evaluation.openai_compatible import InferenceError, OpenAICompatibleInference
from ctxttl.evaluation.ports import AnswerJudgePort, ChatInferencePort
from ctxttl.evaluation.runner import EvaluationInputError, ModelEvaluationRunner

__all__ = [
    "AnswerJudgePort",
    "ChatInferencePort",
    "ChatInferenceRequest",
    "ChatInferenceResponse",
    "CheckpointError",
    "ComparisonInputError",
    "EvaluatedCase",
    "EvaluationCheckpoint",
    "EvaluationInputError",
    "InferenceError",
    "JudgeError",
    "JudgeRequest",
    "JudgeVerdict",
    "LLMAnswerJudge",
    "ModelEvaluationConfig",
    "ModelEvaluationReport",
    "ModelEvaluationRunner",
    "ModelEvaluationSummary",
    "OpenAICompatibleInference",
    "PairedQualityComparison",
    "compare_strategies",
    "normalize_answer",
    "normalized_exact_match",
    "normalized_reference_present",
]
