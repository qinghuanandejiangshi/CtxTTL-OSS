"""MCP-facing use cases mapped onto the stable Core HTTP contract."""

import logging
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter_ns
from typing import Any, TypeVar
from uuid import uuid4

from ctxttl_mcp.core_client import CtxTTLCoreClient, IdentityCoordinates
from ctxttl_mcp.observability import opaque_identity

logger = logging.getLogger("ctxttl_mcp.service")
T = TypeVar("T")


class CtxTTLMCPService:
    """Keep tool semantics independent from the MCP protocol implementation."""

    def __init__(self, client: CtxTTLCoreClient) -> None:
        self._client = client

    async def assert_context(
        self,
        identity: IdentityCoordinates,
        *,
        kind: str,
        subject: str,
        value: Any,
        scope: str,
        applicability: str | None,
        priority: float,
        confidence: float,
        reason: str | None,
        metadata: Mapping[str, Any] | None,
        expires_at: str | None,
        ttl_turns: int | None,
    ) -> dict[str, Any]:
        request_id = self._request_id()
        body = self._context_body(
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
        return await self._logged(
            "ctxttl_context_assert",
            identity,
            request_id,
            lambda: self._client.request(
                "POST",
                "/v1/context/items",
                identity=identity,
                request_id=request_id,
                json_body=body,
            ),
        )

    async def supersede_context(
        self,
        identity: IdentityCoordinates,
        *,
        context_id: str,
        kind: str,
        subject: str,
        value: Any,
        scope: str,
        applicability: str | None,
        priority: float,
        confidence: float,
        reason: str | None,
        metadata: Mapping[str, Any] | None,
        expires_at: str | None,
        ttl_turns: int | None,
    ) -> dict[str, Any]:
        request_id = self._request_id()
        body = self._context_body(
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
        body["supersedes"] = context_id
        return await self._logged(
            "ctxttl_context_supersede",
            identity,
            request_id,
            lambda: self._client.request(
                "POST",
                "/v1/context/items",
                identity=identity,
                request_id=request_id,
                json_body=body,
            ),
        )

    async def end_context(
        self,
        identity: IdentityCoordinates,
        *,
        context_id: str,
        operation: str,
        reason: str | None,
    ) -> dict[str, Any]:
        if operation not in {"retract", "expire"}:
            raise ValueError("operation must be retract or expire")
        request_id = self._request_id()
        return await self._logged(
            f"ctxttl_context_{operation}",
            identity,
            request_id,
            lambda: self._client.request(
                "POST",
                f"/v1/context/items/{context_id}/{operation}",
                identity=identity,
                request_id=request_id,
                json_body={"reason": reason},
            ),
        )

    async def list_context(
        self,
        identity: IdentityCoordinates,
        *,
        status: str | None,
        limit: int,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        return await self._logged(
            "ctxttl_context_list",
            identity,
            None,
            lambda: self._client.request(
                "GET",
                "/v1/context/items",
                identity=identity,
                params=params,
            ),
        )

    async def compile_request(
        self,
        identity: IdentityCoordinates,
        *,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_id = self._request_id()
        return await self._logged(
            "ctxttl_compile_request",
            identity,
            request_id,
            lambda: self._client.request(
                "POST",
                "/v1/context/compile",
                identity=identity,
                request_id=request_id,
                json_body=payload,
            ),
        )

    async def get_trace(
        self,
        identity: IdentityCoordinates,
        *,
        trace_id: str,
    ) -> dict[str, Any]:
        return await self._logged(
            "ctxttl_trace_get",
            identity,
            None,
            lambda: self._client.request(
                "GET",
                f"/v1/traces/{trace_id}",
                identity=identity,
            ),
        )

    @staticmethod
    def _context_body(**values: Any) -> dict[str, Any]:
        return {key: value for key, value in values.items() if value is not None}

    @staticmethod
    def _request_id() -> str:
        return f"mcp_{uuid4().hex}"

    async def _logged(
        self,
        tool: str,
        identity: IdentityCoordinates,
        request_id: str | None,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        started = perf_counter_ns()
        common = {
            "tool": tool,
            "request_id": request_id,
            "session_hash": opaque_identity(identity.session_id),
        }
        logger.info("MCP tool started", extra={"event": "tool_started", **common})
        try:
            result = await operation()
        except Exception as error:
            logger.warning(
                "MCP tool failed",
                extra={
                    "event": "tool_failed",
                    **common,
                    "duration_ms": (perf_counter_ns() - started) / 1_000_000,
                    "error_type": type(error).__name__,
                    "core_error_code": getattr(error, "code", None),
                },
            )
            raise
        logger.info(
            "MCP tool completed",
            extra={
                "event": "tool_completed",
                **common,
                "duration_ms": (perf_counter_ns() - started) / 1_000_000,
            },
        )
        return result
