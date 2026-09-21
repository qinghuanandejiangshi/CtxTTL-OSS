import json
import logging

import httpx
from mcp import Client

from ctxttl_mcp.config import MCPSettings
from ctxttl_mcp.observability import JSONFormatter
from ctxttl_mcp.server import create_mcp_server


async def test_server_exposes_bounded_tool_surface_and_maps_assertion(caplog) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "applied": True,
                "event": {"event_type": "assert"},
                "item": {"id": "ctx_1", "subject": "project.database"},
            },
        )

    server = create_mcp_server(
        MCPSettings(_env_file=None),
        core_transport=httpx.MockTransport(handler),
    )
    caplog.set_level(logging.INFO, logger="ctxttl_mcp.service")
    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool(
            "ctxttl_context_assert",
            {
                "session_id": "session-private-1",
                "task_id": "task-1",
                "agent_id": "researcher",
                "project_id": "project-1",
                "kind": "decision",
                "scope": "task",
                "subject": "project.database",
                "value": "PostgreSQL-secret-value",
                "reason": "production requirement",
            },
        )

    assert [tool.name for tool in tools.tools] == [
        "ctxttl_context_assert",
        "ctxttl_context_supersede",
        "ctxttl_context_retract",
        "ctxttl_context_expire",
        "ctxttl_context_list",
        "ctxttl_compile_request",
        "ctxttl_trace_get",
    ]
    assert result.is_error is False
    assert result.structured_content == {
        "applied": True,
        "event": {"event_type": "assert"},
        "item": {"id": "ctx_1", "subject": "project.database"},
    }
    request = requests[0]
    assert request.url.path == "/v1/context/items"
    assert request.headers["X-CtxTTL-Session-ID"] == "session-private-1"
    assert request.headers["X-CtxTTL-Task-ID"] == "task-1"
    assert request.headers["X-CtxTTL-Agent-ID"] == "researcher"
    assert request.headers["X-CtxTTL-Project-ID"] == "project-1"
    assert request.headers["X-CtxTTL-Request-ID"].startswith("mcp_")
    assert json.loads(request.content)["value"] == "PostgreSQL-secret-value"
    formatter = JSONFormatter()
    rendered_logs = "\n".join(formatter.format(record) for record in caplog.records)
    assert "session-private-1" not in rendered_logs
    assert "PostgreSQL-secret-value" not in rendered_logs


async def test_compile_request_uses_generic_compile_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "payload": json.loads(request.content),
                "trace_id": "trc_1",
                "request_id": request.headers["X-CtxTTL-Request-ID"],
                "metrics": {"compiled_message_tokens": 12},
            },
        )

    server = create_mcp_server(
        MCPSettings(_env_file=None),
        core_transport=httpx.MockTransport(handler),
    )
    payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "preview"}],
    }
    async with Client(server) as client:
        result = await client.call_tool(
            "ctxttl_compile_request",
            {
                "session_id": "session-1",
                "agent_id": "coder",
                "project_id": "project-1",
                "turn_id": "turn-1",
                "payload": payload,
            },
        )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["payload"] == payload
    assert requests[0].url.path == "/v1/context/compile"
    assert requests[0].headers["X-CtxTTL-Turn-ID"] == "turn-1"
    assert requests[0].headers["X-CtxTTL-Agent-ID"] == "coder"
    assert requests[0].headers["X-CtxTTL-Project-ID"] == "project-1"
