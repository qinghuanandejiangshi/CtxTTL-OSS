"""Best-effort extraction of assistant messages from Chat Completions responses."""

import json
import re
from collections.abc import Mapping
from typing import Any


def buffered_assistant_messages(body: bytes, status_code: int) -> list[dict[str, Any]]:
    """Extract complete choice messages without changing the upstream response."""

    if not 200 <= status_code < 300:
        return []
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, Mapping) or not isinstance(payload.get("choices"), list):
        return []
    messages: list[dict[str, Any]] = []
    for choice in payload["choices"]:
        if not isinstance(choice, Mapping) or not isinstance(choice.get("message"), Mapping):
            continue
        message = dict(choice["message"])
        if isinstance(message.get("role"), str):
            messages.append(message)
    return messages


def streaming_assistant_messages(body: bytes, status_code: int) -> list[dict[str, Any]]:
    """Assemble common text and tool-call deltas from a completed SSE response."""

    if not 200 <= status_code < 300:
        return []
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return []
    choices: dict[int, dict[str, Any]] = {}
    tool_calls: dict[int, dict[int, dict[str, Any]]] = {}
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
        if not isinstance(payload, Mapping) or not isinstance(payload.get("choices"), list):
            continue
        for raw_choice in payload["choices"]:
            if not isinstance(raw_choice, Mapping):
                continue
            index = raw_choice.get("index", 0)
            delta = raw_choice.get("delta")
            if not isinstance(index, int) or not isinstance(delta, Mapping):
                continue
            message = choices.setdefault(index, {"role": "assistant"})
            role = delta.get("role")
            if isinstance(role, str):
                message["role"] = role
            for field in ("content", "refusal"):
                fragment = delta.get(field)
                if isinstance(fragment, str):
                    message[field] = f"{message.get(field, '')}{fragment}"
            _merge_function_call(message, delta.get("function_call"))
            _merge_tool_calls(tool_calls.setdefault(index, {}), delta.get("tool_calls"))
    output: list[dict[str, Any]] = []
    for index in sorted(choices):
        message = choices[index]
        calls = tool_calls.get(index)
        if calls:
            message["tool_calls"] = [calls[key] for key in sorted(calls)]
        if len(message) > 1:
            output.append(message)
    return output


def _merge_function_call(message: dict[str, Any], raw_call: Any) -> None:
    if not isinstance(raw_call, Mapping):
        return
    call = message.setdefault("function_call", {})
    for field in ("name", "arguments"):
        fragment = raw_call.get(field)
        if isinstance(fragment, str):
            call[field] = f"{call.get(field, '')}{fragment}"


def _merge_tool_calls(calls: dict[int, dict[str, Any]], raw_calls: Any) -> None:
    if not isinstance(raw_calls, list):
        return
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping):
            continue
        index = raw_call.get("index")
        if not isinstance(index, int):
            continue
        call = calls.setdefault(index, {})
        for field in ("id", "type"):
            value = raw_call.get(field)
            if isinstance(value, str):
                call[field] = value
        function = raw_call.get("function")
        if not isinstance(function, Mapping):
            continue
        target = call.setdefault("function", {})
        name = function.get("name")
        if isinstance(name, str):
            target["name"] = name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            target["arguments"] = f"{target.get('arguments', '')}{arguments}"
