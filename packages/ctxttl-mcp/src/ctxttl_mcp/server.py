"""MCP protocol registration and Streamable HTTP hosting."""

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import JsonValue

from ctxttl_mcp.config import MCPSettings
from ctxttl_mcp.core_client import CtxTTLCoreClient, IdentityCoordinates
from ctxttl_mcp.service import CtxTTLMCPService

_INSTRUCTIONS = (
    "CtxTTL manages explicit lifecycle state for an Agent. Use ctxttl_context_assert only for "
    "durable facts, decisions, constraints, or task state. Use ctxttl_context_supersede for an "
    "explicit correction; never create two active values for one mutable subject. Expire or "
    "retract temporary or withdrawn state. Use compile_request to build the exact model payload. "
    "Always pass stable opaque session/task/turn identifiers and never place credentials in values."
)


@dataclass(frozen=True, slots=True)
class MCPState:
    service: CtxTTLMCPService


def _identity(
    session_id: str,
    user_id: str | None,
    task_id: str | None,
    turn_id: str | None,
    agent_id: str | None = None,
    project_id: str | None = None,
) -> IdentityCoordinates:
    return IdentityCoordinates(
        session_id,
        user_id=user_id,
        task_id=task_id,
        agent_id=agent_id,
        project_id=project_id,
        turn_id=turn_id,
    )


def _service(ctx: Context[Any, Any]) -> CtxTTLMCPService:
    state = ctx.request_context.lifespan_context
    if not isinstance(state, MCPState):
        raise RuntimeError("CtxTTL MCP lifespan state is unavailable")
    return state.service


def create_mcp_server(
    settings: MCPSettings | None = None,
    *,
    core_transport: httpx.AsyncBaseTransport | None = None,
) -> MCPServer[MCPState]:
    """Create the protocol server with a replaceable Core HTTP transport."""

    runtime_settings = settings or MCPSettings()

    @asynccontextmanager
    async def lifespan(_: MCPServer[MCPState]) -> AsyncIterator[MCPState]:
        async with CtxTTLCoreClient(
            runtime_settings,
            transport=core_transport,
        ) as core_client:
            yield MCPState(service=CtxTTLMCPService(core_client))

    server = MCPServer[MCPState](
        name="ctxttl",
        title="CtxTTL Lifecycle Context",
        description="Explicit lifecycle state and context compilation for LLM agents.",
        instructions=_INSTRUCTIONS,
        version="0.1.0",
        lifespan=lifespan,
        log_level=runtime_settings.log_level,
    )

    @server.tool(structured_output=True)
    async def ctxttl_context_assert(
        session_id: str,
        kind: Literal["fact", "decision", "constraint", "task_state"],
        subject: str,
        value: JsonValue,
        ctx: Context[Any, Any],
        scope: Literal["turn", "session", "task", "agent", "project", "user"] = "session",
        applicability: Literal["current_turn", "selective", "required"] | None = None,
        user_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
        priority: float = 0.7,
        confidence: float = 1.0,
        reason: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
        expires_at: str | None = None,
        ttl_turns: int | None = None,
    ) -> dict[str, Any]:
        """Assert one explicit fact, decision, constraint, or task-state item."""

        return await _service(ctx).assert_context(
            _identity(session_id, user_id, task_id, turn_id, agent_id, project_id),
            kind=kind,
            subject=subject,
            value=value,
            scope=scope,
            applicability=applicability,
            priority=priority,
            confidence=confidence,
            reason=reason,
            metadata=metadata,
            expires_at=expires_at,
            ttl_turns=ttl_turns,
        )

    @server.tool(structured_output=True)
    async def ctxttl_context_supersede(
        session_id: str,
        context_id: str,
        kind: Literal["fact", "decision", "constraint", "task_state"],
        subject: str,
        value: JsonValue,
        ctx: Context[Any, Any],
        scope: Literal["turn", "session", "task", "agent", "project", "user"] = "session",
        applicability: Literal["current_turn", "selective", "required"] | None = None,
        user_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
        priority: float = 0.7,
        confidence: float = 1.0,
        reason: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
        expires_at: str | None = None,
        ttl_turns: int | None = None,
    ) -> dict[str, Any]:
        """Replace one reachable active item with a new explicit value."""

        return await _service(ctx).supersede_context(
            _identity(session_id, user_id, task_id, turn_id, agent_id, project_id),
            context_id=context_id,
            kind=kind,
            subject=subject,
            value=value,
            scope=scope,
            applicability=applicability,
            priority=priority,
            confidence=confidence,
            reason=reason,
            metadata=metadata,
            expires_at=expires_at,
            ttl_turns=ttl_turns,
        )

    @server.tool(structured_output=True)
    async def ctxttl_context_retract(
        session_id: str,
        context_id: str,
        ctx: Context[Any, Any],
        reason: str | None = None,
        user_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Withdraw a reachable active item because it is no longer true or authorized."""

        return await _service(ctx).end_context(
            _identity(session_id, user_id, task_id, turn_id, agent_id, project_id),
            context_id=context_id,
            operation="retract",
            reason=reason,
        )

    @server.tool(structured_output=True)
    async def ctxttl_context_expire(
        session_id: str,
        context_id: str,
        ctx: Context[Any, Any],
        reason: str | None = None,
        user_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """End a reachable active item whose planned lifetime has finished."""

        return await _service(ctx).end_context(
            _identity(session_id, user_id, task_id, turn_id, agent_id, project_id),
            context_id=context_id,
            operation="expire",
            reason=reason,
        )

    @server.tool(structured_output=True)
    async def ctxttl_context_list(
        session_id: str,
        ctx: Context[Any, Any],
        status: Literal["active", "superseded", "retracted", "expired"] | None = "active",
        limit: int = 100,
        user_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """List lifecycle state reachable from the supplied identity coordinates."""

        return await _service(ctx).list_context(
            _identity(session_id, user_id, task_id, turn_id, agent_id, project_id),
            status=status,
            limit=limit,
        )

    @server.tool(structured_output=True)
    async def ctxttl_compile_request(
        session_id: str,
        payload: dict[str, Any],
        ctx: Context[Any, Any],
        user_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Compile a request, recording its trace and lifecycle observations, without model I/O."""

        return await _service(ctx).compile_request(
            _identity(session_id, user_id, task_id, turn_id, agent_id, project_id),
            payload=payload,
        )

    @server.tool(structured_output=True)
    async def ctxttl_trace_get(
        session_id: str,
        trace_id: str,
        ctx: Context[Any, Any],
        user_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Read one reachable compilation trace for diagnosis and replay planning."""

        return await _service(ctx).get_trace(
            _identity(session_id, user_id, task_id, turn_id, agent_id, project_id),
            trace_id=trace_id,
        )

    return server


class BearerTokenMiddleware:
    """Minimal static bearer protection for local and controlled HTTP deployments."""

    def __init__(self, app: Any, token: str | None) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if self._token is not None and scope.get("type") == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            supplied = headers.get(b"authorization", b"").decode("latin-1")
            expected = f"Bearer {self._token}"
            if not hmac.compare_digest(supplied, expected):
                body = b'{"error":"unauthorized"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self._app(scope, receive, send)


def create_http_app(
    settings: MCPSettings | None = None,
    *,
    core_transport: httpx.AsyncBaseTransport | None = None,
) -> Any:
    """Create the authenticated Streamable HTTP ASGI application."""

    runtime_settings = settings or MCPSettings()
    server = create_mcp_server(runtime_settings, core_transport=core_transport)
    app = server.streamable_http_app(
        streamable_http_path=runtime_settings.path,
        max_request_body_size=runtime_settings.max_request_body_bytes,
        host=runtime_settings.host,
    )
    token = (
        runtime_settings.bearer_token.get_secret_value()
        if runtime_settings.bearer_token is not None
        else None
    )
    return BearerTokenMiddleware(app, token)
