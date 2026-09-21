"""Lossless Responses-to-compiler adaptation for explicit stateless input."""

import json
from collections.abc import Mapping
from typing import Any

from ctxttl.compiler import ProtocolViolation

_SOURCE_KEY = "_ctxttl_responses_source"
_INSTRUCTIONS_SOURCE = "instructions"
_MESSAGE_ROLES = {"system", "developer", "user", "assistant"}


class ResponsesProtocolAdapter:
    """Expose explicit Responses input to the existing deterministic message compiler.

    Opaque non-message items are represented as mandatory developer units while compiling, then
    restored byte-for-JSON. This preserves reasoning/tool item order and prevents partial removal
    of protocol structures the compiler does not own.
    """

    def to_compiler_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_hidden_state(payload)
        output = dict(payload)
        messages: list[dict[str, Any]] = []
        instructions = payload.get("instructions")
        if instructions is not None:
            if not isinstance(instructions, str | list):
                raise ProtocolViolation("Responses instructions must be a string or an array")
            messages.append(
                {
                    "role": "developer",
                    "content": self._compiler_content(instructions),
                    _SOURCE_KEY: _INSTRUCTIONS_SOURCE,
                }
            )

        raw_input = payload.get("input", [])
        if isinstance(raw_input, str):
            messages.append(
                {
                    "role": "user",
                    "content": raw_input,
                    _SOURCE_KEY: 0,
                }
            )
        elif isinstance(raw_input, list):
            for index, item in enumerate(raw_input):
                if not isinstance(item, Mapping):
                    raise ProtocolViolation(f"Responses input[{index}] must be an object")
                role = item.get("role")
                if isinstance(role, str) and role in _MESSAGE_ROLES:
                    message = dict(item)
                    message[_SOURCE_KEY] = index
                    messages.append(message)
                else:
                    messages.append(
                        {
                            "role": "developer",
                            "content": json.dumps(
                                dict(item),
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            _SOURCE_KEY: index,
                        }
                    )
        else:
            raise ProtocolViolation("Responses input must be a string or an array")

        output["messages"] = messages
        return output

    def from_compiler_payload(
        self,
        original_payload: Mapping[str, Any],
        compiler_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw_messages = compiler_payload.get("messages")
        if not isinstance(raw_messages, list):
            raise ProtocolViolation("compiled Responses messages must be an array")
        raw_input = original_payload.get("input", [])
        originals: list[Any]
        if isinstance(raw_input, str):
            originals = [{"role": "user", "content": raw_input}]
        elif isinstance(raw_input, list):
            originals = list(raw_input)
        else:
            raise ProtocolViolation("Responses input must be a string or an array")

        compiled_input: list[dict[str, Any]] = []
        for index, message in enumerate(raw_messages):
            if not isinstance(message, Mapping):
                raise ProtocolViolation(f"compiled Responses messages[{index}] must be an object")
            source = message.get(_SOURCE_KEY)
            if source == _INSTRUCTIONS_SOURCE:
                continue
            if isinstance(source, int) and not isinstance(source, bool):
                if source < 0 or source >= len(originals):
                    raise ProtocolViolation("compiled Responses source index is invalid")
                original = originals[source]
                if not isinstance(original, Mapping):
                    raise ProtocolViolation("compiled Responses source item must be an object")
                compiled_input.append(dict(original))
                continue
            role = message.get("role")
            content = message.get("content")
            if not isinstance(role, str) or not isinstance(content, str | list):
                raise ProtocolViolation("rendered Responses context must be a message")
            compiled_input.append({"role": role, "content": content})

        output = dict(original_payload)
        output["input"] = compiled_input
        return output

    @staticmethod
    def _reject_hidden_state(payload: Mapping[str, Any]) -> None:
        hidden = [
            name
            for name in ("previous_response_id", "conversation", "prompt")
            if payload.get(name) is not None
        ]
        if hidden:
            fields = ", ".join(hidden)
            raise ProtocolViolation(
                "CtxTTL cannot compile upstream-hidden Responses state; send explicit stateless "
                f"input without: {fields}"
            )

    @staticmethod
    def _compiler_content(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
