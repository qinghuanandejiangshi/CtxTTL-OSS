"""HTTP contract client for CtxTTL Core."""

import logging
from collections.abc import Mapping
from typing import Any, Self

import httpx

from ctxttl_mcp.config import MCPSettings
from ctxttl_mcp.observability import opaque_identity

logger = logging.getLogger("ctxttl_mcp.core")


class CoreClientError(RuntimeError):
    """Base error raised by the CtxTTL Core boundary."""


class CoreUnavailable(CoreClientError):
    """Raised when the Core service cannot be reached."""


class CoreProtocolError(CoreClientError):
    """Raised when Core violates its documented JSON response contract."""


class CoreRejected(CoreClientError):
    """Raised when Core safely rejects an MCP operation."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class CtxTTLCoreClient:
    """Call Core through its public HTTP API without importing Core internals."""

    def __init__(
        self,
        settings: MCPSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        headers = {"User-Agent": "ctxttl-mcp/0.1.0"}
        if self._settings.core_bearer_token is not None:
            headers["Authorization"] = (
                f"Bearer {self._settings.core_bearer_token.get_secret_value()}"
            )
        self._client = httpx.AsyncClient(
            base_url=self._settings.core_url,
            timeout=self._settings.timeout_seconds,
            headers=headers,
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        identity: "IdentityCoordinates",
        request_id: str | None = None,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("CtxTTLCoreClient must be used as an async context manager")
        headers = identity.headers()
        if request_id is not None:
            headers["X-CtxTTL-Request-ID"] = request_id
        try:
            response = await self._client.request(
                method,
                path,
                headers=headers,
                json=dict(json_body) if json_body is not None else None,
                params=params,
            )
        except httpx.HTTPError as error:
            logger.error(
                "CtxTTL Core request failed",
                extra={
                    "event": "core_transport_error",
                    "method": method,
                    "path": path,
                    "session_hash": opaque_identity(identity.session_id),
                    "error_type": type(error).__name__,
                },
            )
            raise CoreUnavailable("CtxTTL Core is unavailable") from error

        try:
            body = response.json()
        except ValueError as error:
            raise CoreProtocolError(
                f"CtxTTL Core returned non-JSON content with status {response.status_code}"
            ) from error
        if not isinstance(body, dict):
            raise CoreProtocolError("CtxTTL Core response must be a JSON object")
        if response.is_error:
            error_body = body.get("error")
            if isinstance(error_body, dict):
                code = str(error_body.get("code") or "core_rejected")
                message = str(error_body.get("message") or "CtxTTL Core rejected the request")
            else:
                code = "core_rejected"
                message = "CtxTTL Core rejected the request"
            raise CoreRejected(response.status_code, code, message)
        return body


class IdentityCoordinates:
    """Small immutable identity value object shared by all MCP operations."""

    __slots__ = (
        "agent_id",
        "project_id",
        "session_id",
        "task_id",
        "turn_id",
        "user_id",
    )

    def __init__(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.task_id = task_id
        self.agent_id = agent_id
        self.project_id = project_id
        self.turn_id = turn_id

    def headers(self) -> dict[str, str]:
        headers = {"X-CtxTTL-Session-ID": self.session_id}
        for name, value in (
            ("X-CtxTTL-User-ID", self.user_id),
            ("X-CtxTTL-Task-ID", self.task_id),
            ("X-CtxTTL-Agent-ID", self.agent_id),
            ("X-CtxTTL-Project-ID", self.project_id),
            ("X-CtxTTL-Turn-ID", self.turn_id),
        ):
            if value is not None:
                headers[name] = value
        return headers
