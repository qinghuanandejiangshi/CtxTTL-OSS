"""Chat Completions response capture tests."""

import json

from ctxttl.application.chat_capture import (
    buffered_assistant_messages,
    streaming_assistant_messages,
)


def test_buffered_capture_preserves_complete_assistant_message() -> None:
    message = {
        "role": "assistant",
        "content": "Use PostgreSQL.",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "save", "arguments": '{"value":1}'},
            }
        ],
    }
    body = json.dumps({"choices": [{"index": 0, "message": message}]}).encode()

    assert buffered_assistant_messages(body, 200) == [message]
    assert buffered_assistant_messages(body, 429) == []
    assert buffered_assistant_messages(b"not-json", 200) == []


def test_streaming_capture_reassembles_text_and_tool_calls() -> None:
    body = b"".join(
        [
            b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"content":"Use "}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"content":"PostgreSQL."}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,',
            b'"id":"call-1","type":"function","function":{"name":"save",',
            b'"arguments":"{\\"value\\":"}}]}}]}\n\n',
            b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,',
            b'"function":{"arguments":"1}"}}]}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
    )

    assert streaming_assistant_messages(body, 200) == [
        {
            "role": "assistant",
            "content": "Use PostgreSQL.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "save", "arguments": '{"value":1}'},
                }
            ],
        }
    ]


def test_streaming_capture_ignores_malformed_and_empty_events() -> None:
    body = b'data: not-json\n\ndata: {"choices":[]}\n\ndata: [DONE]\n\n'

    assert streaming_assistant_messages(body, 200) == []
    assert streaming_assistant_messages(body, 500) == []
