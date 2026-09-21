"""OpenAI-compatible adapter for benchmark inference calls."""

from time import perf_counter_ns
from typing import Any

import httpx

from ctxttl.evaluation.models import ChatInferenceRequest, ChatInferenceResponse
from ctxttl.evaluation.ports import ChatInferencePort


class InferenceError(RuntimeError):
    """Raised when an evaluation provider cannot return a valid completion."""


class OpenAICompatibleInference(ChatInferencePort):
    """Call a buffered Chat Completions endpoint behind the inference port."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("evaluation base_url must use http:// or https://")
        if not api_key:
            raise ValueError("evaluation api_key cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("evaluation timeout must be positive")
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def complete(self, request: ChatInferenceRequest) -> ChatInferenceResponse:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": list(request.messages),
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        reserved = request.provider_options.keys() & payload.keys()
        if reserved:
            names = ", ".join(sorted(reserved))
            raise InferenceError(f"provider_options cannot override reserved fields: {names}")
        payload.update(request.provider_options)
        started_ns = perf_counter_ns()
        try:
            response = await self._client.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        except httpx.HTTPError as error:
            raise InferenceError(f"evaluation provider request failed: {error}") from error
        latency_ms = (perf_counter_ns() - started_ns) / 1_000_000
        if response.is_error:
            safe_error = response.text.replace(self._api_key, "***")
            raise InferenceError(
                f"evaluation provider returned HTTP {response.status_code}: {safe_error[:500]}"
            )
        try:
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message content is not text")
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise InferenceError(
                "evaluation provider returned an invalid completion body"
            ) from error
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return ChatInferenceResponse(
            content=content,
            model=body.get("model") if isinstance(body.get("model"), str) else None,
            response_id=body.get("id") if isinstance(body.get("id"), str) else None,
            finish_reason=(
                choice.get("finish_reason")
                if isinstance(choice.get("finish_reason"), str)
                else None
            ),
            prompt_tokens=self._optional_token_count(usage.get("prompt_tokens")),
            completion_tokens=self._optional_token_count(usage.get("completion_tokens")),
            latency_ms=latency_ms,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _optional_token_count(value: object) -> int | None:
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )
