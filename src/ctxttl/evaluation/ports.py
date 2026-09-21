"""Replaceable boundaries for inference and answer evaluation."""

from typing import Protocol

from ctxttl.evaluation.models import (
    ChatInferenceRequest,
    ChatInferenceResponse,
    JudgeRequest,
    JudgeVerdict,
)


class ChatInferencePort(Protocol):
    async def complete(self, request: ChatInferenceRequest) -> ChatInferenceResponse: ...

    async def aclose(self) -> None: ...


class AnswerJudgePort(Protocol):
    async def judge(self, request: JudgeRequest) -> JudgeVerdict: ...

    async def aclose(self) -> None: ...
