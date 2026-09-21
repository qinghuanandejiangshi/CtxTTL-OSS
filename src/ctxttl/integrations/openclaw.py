"""OpenClaw identity mapping for the provider-neutral Agent context port."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from ctxttl.application.agent_context import AgentContextPort, AgentContextRequest
from ctxttl.application.context_compilation import CompiledChatPayload
from ctxttl.application.transcript_lifecycle import (
    InactiveTranscriptTurn,
    LifecycleFilterResult,
    TranscriptLifecycleFilter,
)

_TURN_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TURN_MARKER_TEMPLATE = "[[ctxttl-turn:{key}]]"
_INACTIVE_TURNS_PATTERN = re.compile(r"\[\[ctxttl-inactive-turns:([^\]]*)\]\]")
_LIFECYCLE_REASONS = frozenset(
    {"expired", "withdrawn", "superseded", "turn_only", "cross_task", "inactive"}
)


@dataclass(frozen=True, slots=True)
class OpenClawTurnContext:
    """Stable OpenClaw coordinates supplied by the runtime integration."""

    agent_id: str
    session_key: str
    channel_id: str | None = None
    account_id: str | None = None
    sender_id: str | None = None
    task_key: str | None = None
    turn_key: str | None = None
    request_key: str | None = None

    def __post_init__(self) -> None:
        required = {"agent_id", "session_key"}
        for name in (
            "agent_id",
            "session_key",
            "channel_id",
            "account_id",
            "sender_id",
            "task_key",
            "turn_key",
            "request_key",
        ):
            value = getattr(self, name)
            if value is None and name not in required:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string when provided")


class OpenClawCompilationInput(BaseModel):
    """HTTP-safe envelope accepted by the optional OpenClaw integration route."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    agent_id: str
    session_key: str
    channel_id: str | None = None
    account_id: str | None = None
    sender_id: str | None = None
    task_key: str | None = None
    turn_key: str | None = None
    request_key: str | None = None

    def turn_context(self) -> OpenClawTurnContext:
        return OpenClawTurnContext(
            agent_id=self.agent_id,
            session_key=self.session_key,
            channel_id=self.channel_id,
            account_id=self.account_id,
            sender_id=self.sender_id,
            task_key=self.task_key,
            turn_key=self.turn_key,
            request_key=self.request_key,
        )


class OpenClawContextAdapter:
    """Translate OpenClaw coordinates without coupling CtxTTL to OpenClaw internals."""

    _NAMESPACE = "openclaw"

    def __init__(self, context_port: AgentContextPort) -> None:
        self._context_port = context_port

    async def compile(
        self,
        payload: Mapping[str, Any],
        context: OpenClawTurnContext,
    ) -> CompiledChatPayload:
        """Compile one OpenAI-compatible request using opaque, stable identities."""

        return await self._context_port.compile(self.request(payload, context))

    def request(
        self,
        payload: Mapping[str, Any],
        context: OpenClawTurnContext,
    ) -> AgentContextRequest:
        """Map framework coordinates into the provider-neutral request contract."""

        return AgentContextRequest(
            payload=payload,
            agent_id=self._stable_id("agent", context.agent_id),
            session_id=self._stable_id(
                "session",
                context.agent_id,
                context.channel_id,
                context.account_id,
                context.session_key,
            ),
            user_id=(
                self._stable_id(
                    "user",
                    context.agent_id,
                    context.channel_id,
                    context.account_id,
                    context.sender_id,
                )
                if context.sender_id is not None
                else None
            ),
            task_id=(
                self._stable_id("task", context.agent_id, context.task_key)
                if context.task_key is not None
                else None
            ),
            turn_id=(
                self._stable_id(
                    "turn",
                    context.agent_id,
                    context.session_key,
                    context.turn_key,
                )
                if context.turn_key is not None
                else None
            ),
            request_id=(
                self._stable_id(
                    "request",
                    context.agent_id,
                    context.session_key,
                    context.request_key,
                )
                if context.request_key is not None
                else None
            ),
        )

    @staticmethod
    def parse_inactive_turn_keys(value: str | None) -> tuple[str, ...]:
        """Parse a bounded header value containing opaque inactive turn keys."""

        if value is None or not value.strip() or value.strip().lower() == "none":
            return ()
        keys = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
        if len(keys) > 100:
            raise ValueError("inactive turn key count cannot exceed 100")
        if any(_TURN_KEY_PATTERN.fullmatch(key) is None for key in keys):
            raise ValueError("inactive turn keys contain unsupported characters")
        return keys

    @classmethod
    def parse_inactive_turns(cls, value: str | None) -> tuple[tuple[str, str], ...]:
        """Parse `reason=turn-key` entries while accepting legacy bare keys."""

        if value is None or not value.strip() or value.strip().lower() == "none":
            return ()
        entries: list[tuple[str, str]] = []
        for raw in (part.strip() for part in value.split(",") if part.strip()):
            prefix, separator, remainder = raw.partition("=")
            reason = prefix if separator and prefix in _LIFECYCLE_REASONS else "inactive"
            key = remainder if reason != "inactive" or (separator and prefix == "inactive") else raw
            if _TURN_KEY_PATTERN.fullmatch(key) is None:
                raise ValueError("inactive turn keys contain unsupported characters")
            entry = (key, reason)
            if entry not in entries:
                entries.append(entry)
        if len(entries) > 100:
            raise ValueError("inactive turn key count cannot exceed 100")
        return tuple(entries)

    @classmethod
    def extract_inline_lifecycle(
        cls,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Consume the latest atomic lifecycle declaration from user messages."""

        output, latest_value = cls._extract_inline_lifecycle_value(payload)
        return output, tuple(key for key, _ in cls.parse_inactive_turns(latest_value))

    @classmethod
    def extract_inline_lifecycle_with_reasons(
        cls,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], tuple[tuple[str, str], ...]]:
        """Consume a declaration while preserving each lifecycle reason."""

        output, latest_value = cls._extract_inline_lifecycle_value(payload)
        return output, cls.parse_inactive_turns(latest_value)

    @staticmethod
    def _extract_inline_lifecycle_value(
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        """Remove integration control markers and return the latest raw value."""

        output = dict(payload)
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            return output, ()

        latest_value: str | None = None
        cleaned: list[Any] = []
        for message in messages:
            if not isinstance(message, Mapping):
                cleaned.append(message)
                continue
            copied = dict(message)
            content = copied.get("content")
            if copied.get("role") == "user" and isinstance(content, str):
                matches = tuple(_INACTIVE_TURNS_PATTERN.finditer(content))
                if matches:
                    latest_value = matches[-1].group(1)
                copied["content"] = _INACTIVE_TURNS_PATTERN.sub("", content).lstrip("\r\n")
            cleaned.append(copied)
        output["messages"] = cleaned
        return output, latest_value

    @staticmethod
    def apply_message_lifecycle(
        payload: Mapping[str, Any],
        inactive_turn_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        """Remove complete transcript turns declared inactive by the Agent runtime."""

        return OpenClawContextAdapter.apply_message_lifecycle_with_report(
            payload,
            tuple((key, "inactive") for key in inactive_turn_keys),
        ).payload

    @staticmethod
    def apply_message_lifecycle_with_report(
        payload: Mapping[str, Any],
        inactive_turns: tuple[tuple[str, str], ...],
    ) -> LifecycleFilterResult:
        """Translate OpenClaw turn keys into the generic lifecycle filter contract."""

        turns = tuple(
            InactiveTranscriptTurn(
                marker=_TURN_MARKER_TEMPLATE.format(key=key),
                reason=reason,
            )
            for key, reason in inactive_turns
        )
        return TranscriptLifecycleFilter().apply(payload, turns)

    @classmethod
    def _stable_id(cls, kind: str, *parts: str | None) -> str:
        canonical = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        return f"{cls._NAMESPACE}:{kind}:{digest}"
