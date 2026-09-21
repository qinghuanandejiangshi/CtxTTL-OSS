from collections.abc import AsyncIterator, Mapping
from types import SimpleNamespace
from typing import Any

from ctxttl.api.app import _proxy_response
from ctxttl.application.ports import StreamingProviderResponse


async def test_relay_closes_capture_iterator_when_client_stops_early() -> None:
    body_closed = False
    upstream_closed = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal body_closed
        try:
            yield b"first"
            yield b"second"
        finally:
            body_closed = True

    async def close_upstream() -> None:
        nonlocal upstream_closed
        upstream_closed = True

    class Proxy:
        def prepare(
            self,
            payload: Mapping[str, Any],
            request_headers: Mapping[str, str],
            *,
            pre_compilation_exclusion: object | None = None,
        ) -> SimpleNamespace:
            return SimpleNamespace(
                mode=SimpleNamespace(value="compile"),
                request_id="request-1",
            )

        async def stream(self, prepared: object) -> StreamingProviderResponse:
            return StreamingProviderResponse(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                body=body(),
                close_callback=close_upstream,
            )

    response = await _proxy_response(Proxy(), {"stream": True}, {})  # type: ignore[arg-type]
    iterator = response.body_iterator

    assert await anext(iterator) == b"first"
    await iterator.aclose()  # type: ignore[attr-defined]

    assert body_closed is True
    assert upstream_closed is True
