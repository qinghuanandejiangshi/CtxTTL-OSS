"""Model-backed evaluation across the existing context strategies."""

from collections import defaultdict
from collections.abc import Callable, Sequence
from statistics import fmean
from typing import Any

from ctxttl.benchmarks.models import BenchmarkCase, StrategyName
from ctxttl.benchmarks.runner import benchmark_dataset_hash
from ctxttl.benchmarks.strategies import BenchmarkStrategy, default_strategies
from ctxttl.evaluation.metrics import normalized_exact_match, normalized_reference_present
from ctxttl.evaluation.models import (
    ChatInferenceRequest,
    EvaluatedCase,
    JudgeRequest,
    ModelEvaluationConfig,
    ModelEvaluationReport,
    ModelEvaluationSummary,
)
from ctxttl.evaluation.ports import AnswerJudgePort, ChatInferencePort


class EvaluationInputError(ValueError):
    """Raised before paid model calls when a dataset lacks evaluation fields."""


class ModelEvaluationRunner:
    """Generate and judge answers without coupling strategies to any provider."""

    def __init__(
        self,
        inference: ChatInferencePort,
        judge: AnswerJudgePort,
        strategies: Sequence[BenchmarkStrategy] | None = None,
    ) -> None:
        self._inference = inference
        self._judge = judge
        self._strategies = tuple(strategies or default_strategies())
        names = [strategy.name for strategy in self._strategies]
        if not names:
            raise ValueError("at least one evaluation strategy is required")
        if len(names) != len(set(names)):
            raise ValueError("evaluation strategy names must be unique")

    async def run(
        self,
        cases: Sequence[BenchmarkCase],
        config: ModelEvaluationConfig,
        *,
        existing_results: Sequence[EvaluatedCase] = (),
        on_result: Callable[[EvaluatedCase], None] | None = None,
    ) -> ModelEvaluationReport:
        prepared = self._prepare_cases(cases)
        prepared_runs = [
            (case, question, reference_answer, strategy.run(case))
            for case, question, reference_answer in prepared
            for strategy in self._strategies
        ]
        result_map: dict[tuple[str, StrategyName, int], EvaluatedCase] = {}
        for result in existing_results:
            key = (result.case_id, result.strategy, result.sample_index)
            if key in result_map:
                raise EvaluationInputError(f"duplicate existing evaluation result: {key}")
            result_map[key] = result
        expected_keys = {
            (case.id, strategy_result.strategy, sample_index)
            for case, _, _, strategy_result in prepared_runs
            for sample_index in range(config.samples_per_case)
        }
        if not set(result_map) <= expected_keys:
            raise EvaluationInputError("existing results do not match the requested experiment")
        for case, question, reference_answer, strategy_result in prepared_runs:
            for sample_index in range(config.samples_per_case):
                key = (case.id, strategy_result.strategy, sample_index)
                if key in result_map:
                    continue
                seed = config.seed + sample_index if config.seed is not None else None
                response = await self._inference.complete(
                    ChatInferenceRequest(
                        model=config.subject_model,
                        messages=(
                            {"role": "system", "content": config.subject_instruction},
                            *strategy_result.messages,
                        ),
                        temperature=config.temperature,
                        seed=seed,
                        max_output_tokens=config.max_output_tokens,
                        provider_options=config.subject_provider_options,
                    )
                )
                verdict = await self._judge.judge(
                    JudgeRequest(
                        case_id=case.id,
                        question=question,
                        reference_answer=reference_answer,
                        candidate_answer=response.content,
                    )
                )
                evaluated = EvaluatedCase(
                    case_id=case.id,
                    strategy=strategy_result.strategy,
                    sample_index=sample_index,
                    selected_context_ids=strategy_result.selected_context_ids,
                    estimated_input_tokens=strategy_result.estimated_tokens,
                    answer=response.content,
                    normalized_exact_match=normalized_exact_match(
                        response.content, reference_answer
                    ),
                    normalized_reference_present=normalized_reference_present(
                        response.content, reference_answer
                    ),
                    subject_response=response,
                    verdict=verdict,
                )
                result_map[key] = evaluated
                if on_result is not None:
                    on_result(evaluated)
        results = [
            result_map[(case.id, strategy_result.strategy, sample_index)]
            for case, _, _, strategy_result in prepared_runs
            for sample_index in range(config.samples_per_case)
        ]
        return ModelEvaluationReport(
            dataset_sha256=benchmark_dataset_hash(cases),
            dataset_case_count=len(cases),
            config=config,
            summaries=self._summaries(results, config),
            cases=tuple(results),
        )

    @classmethod
    def _prepare_cases(
        cls,
        cases: Sequence[BenchmarkCase],
    ) -> list[tuple[BenchmarkCase, str, Any]]:
        if not cases:
            raise EvaluationInputError("at least one evaluation case is required")
        identifiers = [case.id for case in cases]
        if len(identifiers) != len(set(identifiers)):
            raise EvaluationInputError("evaluation case IDs must be unique")
        prepared: list[tuple[BenchmarkCase, str, Any]] = []
        for case in cases:
            question = cls._last_user_text(case)
            if not question:
                raise EvaluationInputError(f"case {case.id} has no text user question")
            if "gold_answer" not in case.metadata:
                raise EvaluationInputError(f"case {case.id} metadata requires gold_answer")
            prepared.append((case, question, case.metadata["gold_answer"]))
        return prepared

    @staticmethod
    def _last_user_text(case: BenchmarkCase) -> str:
        for message in reversed(case.messages):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                return message["content"].strip()
        return ""

    @staticmethod
    def _summaries(
        results: Sequence[EvaluatedCase],
        config: ModelEvaluationConfig,
    ) -> tuple[ModelEvaluationSummary, ...]:
        grouped: dict[StrategyName, list[EvaluatedCase]] = defaultdict(list)
        for result in results:
            grouped[result.strategy].append(result)
        summaries: list[ModelEvaluationSummary] = []
        for strategy in sorted(grouped, key=lambda value: value.value):
            values = grouped[strategy]
            prompt_tokens = [value.subject_response.prompt_tokens for value in values]
            completion_tokens = [value.subject_response.completion_tokens for value in values]
            judge_prompt_tokens = [value.verdict.prompt_tokens for value in values]
            judge_completion_tokens = [value.verdict.completion_tokens for value in values]
            prompt_total = ModelEvaluationRunner._complete_sum(prompt_tokens)
            completion_total = ModelEvaluationRunner._complete_sum(completion_tokens)
            judge_prompt_total = ModelEvaluationRunner._complete_sum(judge_prompt_tokens)
            judge_completion_total = ModelEvaluationRunner._complete_sum(judge_completion_tokens)
            subject_cost = ModelEvaluationRunner._cost(
                prompt_total,
                completion_total,
                config.subject_input_price_per_million_usd,
                config.subject_output_price_per_million_usd,
            )
            judge_cost = ModelEvaluationRunner._cost(
                judge_prompt_total,
                judge_completion_total,
                config.judge_input_price_per_million_usd,
                config.judge_output_price_per_million_usd,
            )
            summaries.append(
                ModelEvaluationSummary(
                    strategy=strategy,
                    sample_count=len(values),
                    mean_judge_score=fmean(value.verdict.score for value in values),
                    answer_accuracy=fmean(value.verdict.correct for value in values),
                    normalized_exact_match_accuracy=fmean(
                        value.normalized_exact_match for value in values
                    ),
                    normalized_reference_presence=fmean(
                        value.normalized_reference_present for value in values
                    ),
                    mean_subject_latency_ms=fmean(
                        value.subject_response.latency_ms for value in values
                    ),
                    mean_judge_latency_ms=fmean(value.verdict.latency_ms for value in values),
                    prompt_tokens=prompt_total,
                    completion_tokens=completion_total,
                    judge_prompt_tokens=judge_prompt_total,
                    judge_completion_tokens=judge_completion_total,
                    estimated_subject_cost_usd=subject_cost,
                    estimated_judge_cost_usd=judge_cost,
                    estimated_total_cost_usd=(
                        subject_cost + judge_cost
                        if subject_cost is not None and judge_cost is not None
                        else None
                    ),
                )
            )
        return tuple(summaries)

    @staticmethod
    def _complete_sum(values: Sequence[int | None]) -> int | None:
        if not all(value is not None for value in values):
            return None
        return sum(value for value in values if value is not None)

    @staticmethod
    def _cost(
        input_tokens: int | None,
        output_tokens: int | None,
        input_price: float | None,
        output_price: float | None,
    ) -> float | None:
        if None in (input_tokens, output_tokens, input_price, output_price):
            return None
        assert input_tokens is not None
        assert output_tokens is not None
        assert input_price is not None
        assert output_price is not None
        return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
