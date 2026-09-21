"""Provider-neutral model evaluation adapter tests."""

import json

import httpx
import pytest

from ctxttl.evaluation import (
    ChatInferenceRequest,
    InferenceError,
    OpenAICompatibleInference,
)


async def test_openai_compatible_inference_normalizes_response_and_usage() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "completion-1",
                "model": "served-model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "PostgreSQL"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleInference(
        "https://provider.example/v1/",
        "secret",
        client=client,
    )
    try:
        result = await adapter.complete(
            ChatInferenceRequest(
                model="subject-model",
                messages=({"role": "user", "content": "Which database?"},),
                seed=7,
                max_output_tokens=64,
            )
        )
    finally:
        await adapter.aclose()
        await client.aclose()

    assert captured["authorization"] == "Bearer secret"
    assert captured["payload"] == {
        "model": "subject-model",
        "messages": [{"role": "user", "content": "Which database?"}],
        "temperature": 0.0,
        "max_tokens": 64,
        "seed": 7,
    }
    assert result.content == "PostgreSQL"
    assert result.model == "served-model"
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 3
    assert result.latency_ms >= 0


async def test_openai_compatible_inference_fails_on_http_and_invalid_bodies() -> None:
    responses = iter(
        [
            httpx.Response(429, text="rate limited"),
            httpx.Response(200, json={"choices": []}),
        ]
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(responses)))
    adapter = OpenAICompatibleInference("https://provider.example/v1", "secret", client=client)
    request = ChatInferenceRequest(model="model", messages=())
    try:
        with pytest.raises(InferenceError, match="HTTP 429"):
            await adapter.complete(request)
        with pytest.raises(InferenceError, match="invalid completion body"):
            await adapter.complete(request)
    finally:
        await client.aclose()


async def test_openai_compatible_inference_passes_safe_provider_options() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleInference("https://provider.example", "secret", client=client)
    try:
        await adapter.complete(
            ChatInferenceRequest(
                model="model",
                messages=({"role": "user", "content": "question"},),
                provider_options={"thinking": {"type": "disabled"}},
            )
        )
    finally:
        await client.aclose()

    assert captured["thinking"] == {"type": "disabled"}


async def test_provider_options_cannot_override_portable_request_fields() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    adapter = OpenAICompatibleInference("https://provider.example", "secret", client=client)
    try:
        with pytest.raises(InferenceError, match="reserved fields: model"):
            await adapter.complete(
                ChatInferenceRequest(model="model", messages=(), provider_options={"model": "x"})
            )
    finally:
        await client.aclose()
