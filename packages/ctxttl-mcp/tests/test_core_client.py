import httpx
import pytest

from ctxttl_mcp.config import MCPSettings
from ctxttl_mcp.core_client import (
    CoreProtocolError,
    CoreRejected,
    CtxTTLCoreClient,
    IdentityCoordinates,
)


async def test_client_maps_identity_request_and_core_bearer_headers() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"items": []})

    settings = MCPSettings(
        core_url="https://core.example.test/",
        core_bearer_token="core-secret",
        _env_file=None,
    )
    async with CtxTTLCoreClient(
        settings,
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.request(
            "GET",
            "/v1/context/items",
            identity=IdentityCoordinates(
                "session-1",
                user_id="user-1",
                task_id="task-1",
                agent_id="researcher",
                project_id="project-1",
                turn_id="turn-1",
            ),
            params={"status": "active"},
        )

    assert result == {"items": []}
    request = captured[0]
    assert request.url == "https://core.example.test/v1/context/items?status=active"
    assert request.headers["Authorization"] == "Bearer core-secret"
    assert request.headers["X-CtxTTL-Session-ID"] == "session-1"
    assert request.headers["X-CtxTTL-User-ID"] == "user-1"
    assert request.headers["X-CtxTTL-Task-ID"] == "task-1"
    assert request.headers["X-CtxTTL-Agent-ID"] == "researcher"
    assert request.headers["X-CtxTTL-Project-ID"] == "project-1"
    assert request.headers["X-CtxTTL-Turn-ID"] == "turn-1"


async def test_client_preserves_safe_core_error_code() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "context_conflict",
                    "message": "active subject already exists",
                }
            },
        )

    async with CtxTTLCoreClient(
        MCPSettings(_env_file=None),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(CoreRejected) as caught:
            await client.request(
                "POST",
                "/v1/context/items",
                identity=IdentityCoordinates("session-1"),
                json_body={"value": "not logged"},
            )

    assert caught.value.status_code == 409
    assert caught.value.code == "context_conflict"
    assert str(caught.value) == "active subject already exists"


async def test_client_rejects_non_json_core_response() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"gateway failure")

    async with CtxTTLCoreClient(
        MCPSettings(_env_file=None),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(CoreProtocolError, match="non-JSON"):
            await client.request(
                "GET",
                "/v1/context/items",
                identity=IdentityCoordinates("session-1"),
            )
