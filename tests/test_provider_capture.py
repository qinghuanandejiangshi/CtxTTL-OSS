import json

from ctxttl.application.provider_capture import (
    ProviderUsage,
    chat_usage,
    responses_assistant_messages,
    responses_usage,
    streaming_chat_terminal,
    streaming_responses_assistant_messages,
    streaming_responses_terminal,
    streaming_responses_usage,
)


def _response() -> dict:
    return {
        "id": "resp_1",
        "status": "completed",
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Use PostgreSQL."}],
            },
            {"type": "reasoning", "summary": []},
        ],
        "usage": {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 80},
            "output_tokens": 14,
        },
    }


def test_buffered_responses_capture_extracts_messages_and_usage() -> None:
    body = json.dumps(_response()).encode()

    assert responses_assistant_messages(body, 200) == [
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Use PostgreSQL."}],
        }
    ]
    assert responses_usage(body, 200) == ProviderUsage(120, 80, 14)


def test_streaming_responses_capture_uses_terminal_event() -> None:
    event = {"type": "response.completed", "sequence_number": 8, "response": _response()}
    body = f"event: response.completed\ndata: {json.dumps(event)}\n\n".encode()

    assert streaming_responses_assistant_messages(body, 200)[0]["role"] == "assistant"
    assert streaming_responses_usage(body, 200) == ProviderUsage(120, 80, 14)


def test_streaming_responses_capture_falls_back_to_completed_output_item() -> None:
    message = _response()["output"][0]
    output_item = {
        "type": "response.output_item.done",
        "sequence_number": 7,
        "item": message,
    }
    completed = {
        "type": "response.completed",
        "sequence_number": 8,
        "response": {**_response(), "output": []},
    }
    body = (
        f"event: response.output_item.done\ndata: {json.dumps(output_item)}\n\n"
        f"event: response.completed\ndata: {json.dumps(completed)}\n\n"
    ).encode()

    assert streaming_responses_assistant_messages(body, 200) == [
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Use PostgreSQL."}],
        }
    ]


def test_responses_capture_ignores_errors_and_malformed_content() -> None:
    assert responses_assistant_messages(b"not-json", 200) == []
    assert responses_usage(json.dumps(_response()).encode(), 500) is None
    assert streaming_responses_usage(b"data: not-json\n\n", 200) is None


def test_chat_usage_normalizes_cached_input_tokens() -> None:
    body = json.dumps(
        {
            "usage": {
                "prompt_tokens": 90,
                "prompt_tokens_details": {"cached_tokens": 64},
                "completion_tokens": 12,
            }
        }
    ).encode()

    assert chat_usage(body, 200) == ProviderUsage(90, 64, 12)


def test_stream_terminal_detectors_require_complete_protocol_markers() -> None:
    partial_responses = b'data: {"type":"response.output_text.delta"}\n\n'
    terminal_responses = partial_responses + b'data: {"type":"response.completed"}\n\n'
    partial_chat = b'data: {"choices":[]}\n\n'
    terminal_chat = partial_chat + b"data: [DONE]\n\n"

    assert streaming_responses_terminal(partial_responses, 200) is False
    assert streaming_responses_terminal(terminal_responses, 200) is True
    assert streaming_responses_terminal(terminal_responses, 500) is False
    assert streaming_chat_terminal(partial_chat, 200) is False
    assert streaming_chat_terminal(terminal_chat, 200) is True
    assert streaming_chat_terminal(terminal_chat, 500) is False
