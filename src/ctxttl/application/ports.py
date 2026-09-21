"""Ports owned by the application layer and implemented by infrastructure adapters."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class BufferedProviderResponse:
    """A complete upstream response without HTTP framework dependencies."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes
    trace_id: str | None = None
    compilation_duration_ms: float | None = None
    upstream_response_start_ms: float | None = None
    proxy_response_start_ms: float | None = None


@dataclass(frozen=True, slots=True)
class StreamingProviderResponse:
    """An upstream byte stream whose resources must be closed by the consumer."""

    status_code: int
    headers: Mapping[str, str]
    body: AsyncIterator[bytes]
    close_callback: Callable[[], Awaitable[None]]
    trace_id: str | None = None
    compilation_duration_ms: float | None = None
    upstream_response_start_ms: float | None = None
    proxy_response_start_ms: float | None = None

    async def aclose(self) -> None:
        await self.close_callback()


class ChatCompletionTransport(Protocol):
    """Provider-neutral transport contract for Chat Completions."""

    async def complete(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse: ...

    async def stream(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> StreamingProviderResponse: ...


class ModelCatalogTransport(Protocol):
    """Lossless read-only transport for a provider model catalog."""

    async def list_models(
        self,
        query_params: Sequence[tuple[str, str]],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse: ...
