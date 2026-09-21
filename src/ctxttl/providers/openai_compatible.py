"""HTTPX transports for OpenAI-compatible model endpoints."""

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

from ctxttl.application.ports import BufferedProviderResponse, StreamingProviderResponse
from ctxttl.config import Settings

_FORWARDED_REQUEST_HEADERS = {
    "authorization",
    "idempotency-key",
    "openai-organization",
    "openai-project",
}


class UpstreamConnectionError(RuntimeError):
    """Raised when the configured provider cannot be reached."""


class HttpxOpenAITransport:
    """Lossless JSON and byte-stream transport shared by OpenAI-compatible endpoints."""

    endpoint_path: str

    def __init__(
        self,
        settings: Settings,
        endpoint_path: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self.endpoint_path = endpoint_path.strip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout=settings.upstream_timeout_seconds,
                connect=settings.upstream_connect_timeout_seconds,
            )
        )

    @property
    def endpoint(self) -> str:
        return f"{self._settings.upstream_base_url}/{self.endpoint_path}"

    def _upstream_headers(self, request_headers: Mapping[str, str]) -> dict[str, str]:
        headers = {
            key.lower(): value
            for key, value in request_headers.items()
            if key.lower() in _FORWARDED_REQUEST_HEADERS
        }
        if self._settings.upstream_api_key is not None:
            headers["authorization"] = (
                f"Bearer {self._settings.upstream_api_key.get_secret_value()}"
            )
        return headers

    async def complete(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse:
        try:
            response = await self._client.post(
                self.endpoint,
                json=payload,
                headers=self._upstream_headers(request_headers),
            )
        except httpx.HTTPError as error:
            raise UpstreamConnectionError("unable to reach the upstream provider") from error
        return BufferedProviderResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
        )

    async def list_models(
        self,
        query_params: Sequence[tuple[str, str]],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse:
        """Relay the provider model catalog without coupling it to compilation policy."""

        try:
            response = await self._client.get(
                f"{self._settings.upstream_base_url}/models",
                params=query_params,
                headers=self._upstream_headers(request_headers),
            )
        except httpx.HTTPError as error:
            raise UpstreamConnectionError("unable to reach the upstream provider") from error
        return BufferedProviderResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
        )

    async def stream(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> StreamingProviderResponse:
        request = self._client.build_request(
            "POST",
            self.endpoint,
            json=payload,
            headers=self._upstream_headers(request_headers),
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as error:
            raise UpstreamConnectionError("unable to reach the upstream provider") from error

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            except httpx.HTTPError as error:
                raise UpstreamConnectionError("upstream stream was interrupted") from error

        return StreamingProviderResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body(),
            close_callback=response.aclose,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class HttpxChatCompletionTransport(HttpxOpenAITransport):
    """OpenAI-compatible Chat Completions transport."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings, "chat/completions", client)


class HttpxResponsesTransport(HttpxOpenAITransport):
    """OpenAI-compatible Responses transport."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(settings, "responses", client)
