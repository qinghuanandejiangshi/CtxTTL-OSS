import json

import httpx

from ctxttl.config import Settings
from ctxttl.providers import HttpxChatCompletionTransport, HttpxResponsesTransport


def settings(**overrides: object) -> Settings:
    return Settings(
        upstream_base_url="https://provider.example/v1/",
        _env_file=None,
        **overrides,
    )


async def test_provider_uses_expected_endpoint_and_preserves_payload() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(await request.aread())
        captured["session_header"] = request.headers.get("X-CtxTTL-Session-ID")
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "x-request-id": "req-1"},
            json={"id": "chatcmpl-1", "choices": []},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpxChatCompletionTransport(settings(), client)
    payload = {"model": "test", "messages": [], "unknown": {"preserve": True}}

    response = await transport.complete(
        payload,
        {"Authorization": "Bearer client-key", "X-CtxTTL-Session-ID": "session-1"},
    )
    await client.aclose()

    assert captured["url"] == "https://provider.example/v1/chat/completions"
    assert captured["payload"] == payload
    assert captured["session_header"] is None
    assert response.status_code == 200


async def test_responses_provider_uses_responses_endpoint_losslessly() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(await request.aread())
        return httpx.Response(200, json={"id": "resp_1", "output": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpxResponsesTransport(settings(), client)
    payload = {
        "model": "test",
        "input": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "name": "lookup", "parameters": {}}],
    }

    response = await transport.complete(payload, {"Authorization": "Bearer client-key"})
    await client.aclose()

    assert captured["url"] == "https://provider.example/v1/responses"
    assert captured["payload"] == payload
    assert response.status_code == 200


async def test_provider_relays_model_catalog_query_and_authorization() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"object": "list", "data": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpxResponsesTransport(settings(), client)

    response = await transport.list_models(
        [("client_version", "0.155.0"), ("feature", "responses")],
        {"Authorization": "Bearer client-key", "X-CtxTTL-Session-ID": "private"},
    )
    await client.aclose()

    assert captured["url"] == (
        "https://provider.example/v1/models?client_version=0.155.0&feature=responses"
    )
    assert captured["authorization"] == "Bearer client-key"
    assert response.status_code == 200


async def test_configured_api_key_overrides_client_authorization() -> None:
    captured_authorization: str | None = None
    authorization_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal authorization_count, captured_authorization
        captured_authorization = request.headers.get("Authorization")
        authorization_count = len(request.headers.get_list("Authorization"))
        return httpx.Response(200, json={"choices": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpxChatCompletionTransport(
        settings(upstream_api_key="server-secret"),
        client,
    )

    await transport.complete(
        {"model": "test", "messages": []},
        {"Authorization": "Bearer untrusted-client-key"},
    )
    await client.aclose()

    assert captured_authorization == "Bearer server-secret"
    assert authorization_count == 1


async def test_streaming_provider_releases_http_response() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: first\n\ndata: [DONE]\n\n",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpxChatCompletionTransport(settings(), client)

    response = await transport.stream(
        {"model": "test", "messages": [], "stream": True},
        {"Authorization": "Bearer key"},
    )
    body = b"".join([chunk async for chunk in response.body])
    await response.aclose()
    await client.aclose()

    assert response.status_code == 200
    assert body == b"data: first\n\ndata: [DONE]\n\n"
