"""Provider-response capture without changing bytes returned to callers."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Normalized provider-reported token counters."""

    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None


def chat_usage(body: bytes, status_code: int) -> ProviderUsage | None:
    """Read usage from one buffered Chat Completions response."""

    return _chat_usage_from_payload(_json_object(body, status_code))


def streaming_chat_usage(body: bytes, status_code: int) -> ProviderUsage | None:
    """Read usage from the last Chat Completions SSE event that reports it."""

    usage = None
    for payload in _sse_payloads(body, status_code):
        candidate = _chat_usage_from_payload(payload)
        if candidate is not None:
            usage = candidate
    return usage


def streaming_chat_terminal(body: bytes, status_code: int) -> bool:
    """Return whether a successful Chat Completions stream emitted its terminal marker."""

    if not 200 <= status_code < 300:
        return False
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    for block in re.split(r"\r?\n\r?\n", text):
        data = "\n".join(
            line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
        )
        if data == "[DONE]":
            return True
    return False


def responses_assistant_messages(body: bytes, status_code: int) -> list[dict[str, Any]]:
    """Extract completed assistant message items from a buffered Responses result."""

    return _responses_messages_from_payload(_json_object(body, status_code))


def streaming_responses_assistant_messages(
    body: bytes,
    status_code: int,
) -> list[dict[str, Any]]:
    """Extract assistant messages from a completed Responses SSE stream.

    Some Responses providers include completed output items in
    ``response.output_item.done`` events but omit them from the terminal
    ``response.completed`` snapshot. Prefer the terminal snapshot when it
    contains messages, otherwise retain the completed message items observed
    earlier in the stream.
    """

    completed_item_messages: list[dict[str, Any]] = []
    terminal_messages: list[dict[str, Any]] = []
    for event in _sse_payloads(body, status_code):
        event_type = event.get("type")
        if event_type == "response.output_item.done":
            item = event.get("item")
            message = _responses_message_from_item(item)
            if message is not None:
                completed_item_messages.append(message)
        elif event_type == "response.completed":
            response = event.get("response")
            if isinstance(response, Mapping):
                terminal_messages = _responses_messages_from_payload(response)
    return terminal_messages or completed_item_messages


def responses_usage(body: bytes, status_code: int) -> ProviderUsage | None:
    """Read usage from one buffered Responses result."""

    return _responses_usage_from_payload(_json_object(body, status_code))


def streaming_responses_usage(body: bytes, status_code: int) -> ProviderUsage | None:
    """Read usage from the terminal Responses SSE event."""

    usage = None
    for event in _sse_payloads(body, status_code):
        response = event.get("response")
        if isinstance(response, Mapping):
            candidate = _responses_usage_from_payload(response)
            if candidate is not None:
                usage = candidate
    return usage


def streaming_responses_terminal(body: bytes, status_code: int) -> bool:
    """Return whether a successful Responses stream emitted a terminal response event."""

    if not 200 <= status_code < 300:
        return False
    terminal_types = {
        "response.completed",
        "response.failed",
        "response.incomplete",
        "response.cancelled",
    }
    return any(event.get("type") in terminal_types for event in _sse_payloads(body, status_code))


def _json_object(body: bytes, status_code: int) -> Mapping[str, Any] | None:
    if not 200 <= status_code < 300:
        return None
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _sse_payloads(body: bytes, status_code: int) -> list[Mapping[str, Any]]:
    if not 200 <= status_code < 300:
        return []
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return []
    payloads: list[Mapping[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        if data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            payloads.append(payload)
    return payloads


def _chat_usage_from_payload(payload: Mapping[str, Any] | None) -> ProviderUsage | None:
    if payload is None or not isinstance(payload.get("usage"), Mapping):
        return None
    usage = payload["usage"]
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, Mapping) else None
    return ProviderUsage(
        input_tokens=_token_count(usage.get("prompt_tokens")),
        cached_input_tokens=_token_count(cached),
        output_tokens=_token_count(usage.get("completion_tokens")),
    )


def _responses_usage_from_payload(payload: Mapping[str, Any] | None) -> ProviderUsage | None:
    if payload is None or not isinstance(payload.get("usage"), Mapping):
        return None
    usage = payload["usage"]
    details = usage.get("input_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, Mapping) else None
    return ProviderUsage(
        input_tokens=_token_count(usage.get("input_tokens")),
        cached_input_tokens=_token_count(cached),
        output_tokens=_token_count(usage.get("output_tokens")),
    )


def _responses_messages_from_payload(
    payload: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if payload is None or not isinstance(payload.get("output"), list):
        return []
    messages: list[dict[str, Any]] = []
    for item in payload["output"]:
        message = _responses_message_from_item(item)
        if message is not None:
            messages.append(message)
    return messages


def _responses_message_from_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping) or item.get("type") != "message":
        return None
    role = item.get("role")
    content = item.get("content")
    if not isinstance(role, str) or not isinstance(content, str | list):
        return None
    return {"role": role, "content": content}


def _token_count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
