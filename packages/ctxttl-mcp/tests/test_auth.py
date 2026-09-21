import httpx

from ctxttl_mcp.server import BearerTokenMiddleware


async def _ok_app(scope, receive, send) -> None:
    del scope, receive
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def test_bearer_middleware_rejects_missing_token() -> None:
    app = BearerTokenMiddleware(_ok_app, "expected-token")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/mcp")

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"


async def test_bearer_middleware_accepts_matching_token() -> None:
    app = BearerTokenMiddleware(_ok_app, "expected-token")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer expected-token"},
        )

    assert response.status_code == 204
