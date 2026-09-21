import pytest

from ctxttl.application import ResponsesProtocolAdapter
from ctxttl.compiler import ProtocolViolation


def test_adapter_preserves_explicit_items_and_restores_opaque_items() -> None:
    adapter = ResponsesProtocolAdapter()
    payload = {
        "model": "test-model",
        "instructions": "Follow the project rules.",
        "input": [
            {"role": "user", "content": "Initial request"},
            {"type": "reasoning", "encrypted_content": "opaque"},
            {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "done"},
            {"role": "user", "content": "Final request"},
        ],
        "tools": [{"type": "function", "name": "lookup", "parameters": {}}],
    }

    compiler_payload = adapter.to_compiler_payload(payload)
    assert len(compiler_payload["messages"]) == 6
    assert compiler_payload["messages"][0]["role"] == "developer"
    assert compiler_payload["messages"][2]["role"] == "developer"

    compiled_messages = [
        compiler_payload["messages"][0],
        {"role": "system", "content": "selected lifecycle context"},
        *compiler_payload["messages"][2:],
    ]
    restored = adapter.from_compiler_payload(
        payload,
        {**compiler_payload, "messages": compiled_messages},
    )

    assert restored["instructions"] == payload["instructions"]
    assert restored["tools"] == payload["tools"]
    assert restored["input"][0] == {
        "role": "system",
        "content": "selected lifecycle context",
    }
    assert restored["input"][1:] == payload["input"][1:]
    assert all("_ctxttl_responses_source" not in item for item in restored["input"])


@pytest.mark.parametrize("field", ["previous_response_id", "conversation", "prompt"])
def test_adapter_rejects_upstream_hidden_state(field: str) -> None:
    with pytest.raises(ProtocolViolation, match="upstream-hidden"):
        ResponsesProtocolAdapter().to_compiler_payload(
            {"model": "test-model", "input": "hello", field: "hidden-state"}
        )


def test_adapter_converts_string_input_to_explicit_message_array() -> None:
    adapter = ResponsesProtocolAdapter()
    payload = {"model": "test-model", "input": "hello"}

    compiler_payload = adapter.to_compiler_payload(payload)
    restored = adapter.from_compiler_payload(payload, compiler_payload)

    assert restored["input"] == [{"role": "user", "content": "hello"}]
