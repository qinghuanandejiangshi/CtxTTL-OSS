"""Model-backed benchmark runner and judge tests."""

from datetime import UTC, datetime

import pytest

from ctxttl.benchmarks.models import BenchmarkCase
from ctxttl.benchmarks.strategies import FullHistoryStrategy
from ctxttl.evaluation import (
    ChatInferenceRequest,
    ChatInferenceResponse,
    EvaluationInputError,
    JudgeError,
    JudgeRequest,
    JudgeVerdict,
    LLMAnswerJudge,
    ModelEvaluationConfig,
    ModelEvaluationRunner,
)
from ctxttl.identity import RequestIdentity


class FakeInference:
    def __init__(self, contents: list[str] | None = None) -> None:
        self.contents = iter(contents or ["PostgreSQL"])
        self.requests: list[ChatInferenceRequest] = []

    async def complete(self, request: ChatInferenceRequest) -> ChatInferenceResponse:
        self.requests.append(request)
        return ChatInferenceResponse(
            content=next(self.contents),
            model=request.model,
            response_id=f"response-{len(self.requests)}",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=2,
            latency_ms=5.0,
        )

    async def aclose(self) -> None:
        return None


class FakeJudge:
    def __init__(self) -> None:
        self.requests: list[JudgeRequest] = []

    async def judge(self, request: JudgeRequest) -> JudgeVerdict:
        self.requests.append(request)
        correct = request.candidate_answer == request.reference_answer
        return JudgeVerdict(
            score=1.0 if correct else 0.0,
            correct=correct,
            confidence=1.0,
            rationale="exact test verdict",
            judge_model="fake-judge",
            latency_ms=3.0,
            prompt_tokens=5,
            completion_tokens=1,
        )

    async def aclose(self) -> None:
        return None


def benchmark_case(*, include_gold: bool = True) -> BenchmarkCase:
    metadata = {"gold_answer": "PostgreSQL"} if include_gold else {}
    return BenchmarkCase(
        id="case-1",
        identity=RequestIdentity(session_id="session-1"),
        messages=({"role": "user", "content": "Which database?"},),
        metadata=metadata,
        target_tokens=100,
        max_tokens=200,
        recent_turn_reserve=1,
    )


async def test_model_runner_records_samples_usage_and_aggregate_accuracy() -> None:
    inference = FakeInference(["PostgreSQL", "MySQL"])
    judge = FakeJudge()
    config = ModelEvaluationConfig(
        subject_model="subject-model",
        judge_model="judge-model",
        seed=10,
        samples_per_case=2,
        subject_input_price_per_million_usd=2,
        subject_output_price_per_million_usd=4,
        judge_input_price_per_million_usd=1,
        judge_output_price_per_million_usd=3,
        subject_provider_options={"thinking": {"type": "disabled"}},
    )

    report = await ModelEvaluationRunner(
        inference,
        judge,
        strategies=(FullHistoryStrategy(),),
    ).run([benchmark_case()], config)

    assert [request.seed for request in inference.requests] == [10, 11]
    assert inference.requests[0].provider_options == {"thinking": {"type": "disabled"}}
    assert inference.requests[0].messages[0]["role"] == "system"
    assert "directly and concisely" in inference.requests[0].messages[0]["content"]
    assert [request.candidate_answer for request in judge.requests] == ["PostgreSQL", "MySQL"]
    assert len(report.cases) == 2
    assert report.summaries[0].answer_accuracy == 0.5
    assert report.summaries[0].normalized_exact_match_accuracy == 0.5
    assert report.summaries[0].normalized_reference_presence == 0.5
    assert report.summaries[0].mean_judge_score == 0.5
    assert report.summaries[0].prompt_tokens == 20
    assert report.summaries[0].completion_tokens == 4
    assert report.summaries[0].estimated_subject_cost_usd == 0.000056
    assert report.summaries[0].estimated_judge_cost_usd == 0.000016
    assert report.summaries[0].estimated_total_cost_usd == 0.000072
    assert report.created_at >= datetime(2026, 1, 1, tzinfo=UTC)


async def test_model_runner_resumes_without_repeating_completed_calls() -> None:
    config = ModelEvaluationConfig(
        subject_model="subject-model",
        judge_model="judge-model",
        samples_per_case=2,
    )
    first_inference = FakeInference(["PostgreSQL", "MySQL"])
    first_report = await ModelEvaluationRunner(
        first_inference,
        FakeJudge(),
        strategies=(FullHistoryStrategy(),),
    ).run([benchmark_case()], config)
    resumed_inference = FakeInference(["MySQL"])

    resumed_report = await ModelEvaluationRunner(
        resumed_inference,
        FakeJudge(),
        strategies=(FullHistoryStrategy(),),
    ).run(
        [benchmark_case()],
        config,
        existing_results=(first_report.cases[0],),
    )

    assert len(resumed_inference.requests) == 1
    assert [case.sample_index for case in resumed_report.cases] == [0, 1]
    assert resumed_report.cases[0] == first_report.cases[0]


async def test_model_runner_validates_all_gold_answers_before_paid_calls() -> None:
    inference = FakeInference()
    judge = FakeJudge()

    with pytest.raises(EvaluationInputError, match="gold_answer"):
        await ModelEvaluationRunner(
            inference,
            judge,
            strategies=(FullHistoryStrategy(),),
        ).run(
            [benchmark_case(include_gold=False)],
            ModelEvaluationConfig(
                subject_model="subject",
                judge_model="judge",
            ),
        )

    assert inference.requests == []
    assert judge.requests == []


async def test_llm_judge_uses_strict_json_contract() -> None:
    inference = FakeInference(['{"score":1,"correct":true,"confidence":0.9,"rationale":"matches"}'])
    judge = LLMAnswerJudge(
        inference,
        "judge-model",
        provider_options={"thinking": {"type": "disabled"}},
    )

    verdict = await judge.judge(
        JudgeRequest(
            case_id="case-1",
            question="Which database?",
            reference_answer="PostgreSQL",
            candidate_answer="PostgreSQL",
        )
    )

    assert verdict.correct is True
    assert verdict.judge_model == "judge-model"
    assert verdict.prompt_tokens == 10
    assert verdict.completion_tokens == 2
    assert verdict.judge_prompt_version == "answer-correctness-v2"
    assert inference.requests[0].response_format == {"type": "json_object"}
    assert inference.requests[0].provider_options == {"thinking": {"type": "disabled"}}
    assert inference.requests[0].temperature == 0


async def test_llm_judge_rejects_non_json_verdict() -> None:
    inference = FakeInference(["correct"])

    with pytest.raises(JudgeError, match="invalid verdict JSON"):
        await LLMAnswerJudge(inference, "judge-model").judge(
            JudgeRequest(
                case_id="case-1",
                question="Which database?",
                reference_answer="PostgreSQL",
                candidate_answer="PostgreSQL",
            )
        )
