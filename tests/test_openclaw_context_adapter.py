from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from ctxttl.application import AgentContextRequest
from ctxttl.integrations import OpenClawContextAdapter, OpenClawTurnContext


@dataclass
class RecordingContextPort:
    requests: list[AgentContextRequest] = field(default_factory=list)

    async def compile(self, request: AgentContextRequest) -> Any:
        self.requests.append(request)
        return request


async def compile_request(
    adapter: OpenClawContextAdapter,
    payload: Mapping[str, Any],
    context: OpenClawTurnContext,
) -> AgentContextRequest:
    result = await adapter.compile(payload, context)
    assert isinstance(result, AgentContextRequest)
    return result


async def test_adapter_maps_openclaw_coordinates_to_stable_safe_identities() -> None:
    port = RecordingContextPort()
    adapter = OpenClawContextAdapter(port)
    payload = {"messages": [{"role": "user", "content": "继续"}]}
    context = OpenClawTurnContext(
        agent_id="main/agent",
        session_key="weixin:room/42",
        channel_id="openclaw-weixin",
        account_id="account@example",
        sender_id="wx/user:1",
        task_key="rag/evaluation",
        turn_key="turn/3",
        request_key="message/9",
    )

    first = await compile_request(adapter, payload, context)
    second = await compile_request(adapter, payload, context)

    assert first == second
    assert first.payload is payload
    for value in (
        first.session_id,
        first.user_id,
        first.task_id,
        first.turn_id,
        first.request_id,
    ):
        assert value is not None
        assert value.startswith("openclaw:")
        assert len(value) <= 128


def test_adapter_exposes_provider_neutral_request_mapping() -> None:
    adapter = OpenClawContextAdapter(RecordingContextPort())
    payload = {"messages": [{"role": "user", "content": "继续"}]}
    context = OpenClawTurnContext(agent_id="main", session_key="conversation-1")

    request = adapter.request(payload, context)

    assert request.payload is payload
    assert request.session_id.startswith("openclaw:session:")


def test_adapter_removes_an_entire_inactive_turn_without_orphaning_tools() -> None:
    payload = {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "[[ctxttl-turn:expired-1]] temporary"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read"}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "temporary result"},
            {"role": "assistant", "content": "temporary answer"},
            {"role": "user", "content": "[[ctxttl-turn:active-2]] current"},
            {"role": "assistant", "content": "current answer"},
        ],
    }

    result = OpenClawContextAdapter.apply_message_lifecycle(payload, ("expired-1",))

    assert result["messages"] == [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "[[ctxttl-turn:active-2]] current"},
        {"role": "assistant", "content": "current answer"},
    ]
    assert payload["messages"][1]["content"].endswith("temporary")


def test_adapter_parses_unique_bounded_inactive_turn_keys() -> None:
    assert OpenClawContextAdapter.parse_inactive_turn_keys("expired-1, stale:2,expired-1") == (
        "expired-1",
        "stale:2",
    )
    assert OpenClawContextAdapter.parse_inactive_turn_keys(None) == ()
    assert OpenClawContextAdapter.parse_inactive_turn_keys("none") == ()

    with pytest.raises(ValueError, match="unsupported characters"):
        OpenClawContextAdapter.parse_inactive_turn_keys("not allowed")


def test_adapter_preserves_explicit_lifecycle_reasons_and_legacy_keys() -> None:
    assert OpenClawContextAdapter.parse_inactive_turns(
        "expired=old-1,superseded=old-2,legacy-3"
    ) == (
        ("old-1", "expired"),
        ("old-2", "superseded"),
        ("legacy-3", "inactive"),
    )


def test_adapter_reports_tokens_removed_by_lifecycle_reason() -> None:
    payload = {
        "messages": [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "[[ctxttl-turn:old-1]] expired"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "[[ctxttl-turn:old-2]] replaced"},
            {"role": "assistant", "content": "replaced answer"},
            {"role": "user", "content": "[[ctxttl-turn:now]] current"},
        ]
    }

    result = OpenClawContextAdapter.apply_message_lifecycle_with_report(
        payload,
        (("old-1", "expired"), ("old-2", "superseded")),
    )

    assert len(result.payload["messages"]) == 2
    assert result.exclusion.message_count == 4
    assert result.exclusion.token_count > 0
    assert result.exclusion.message_counts_by_reason == {"expired": 2, "superseded": 2}
    assert set(result.exclusion.tokens_by_reason) == {"expired", "superseded"}


def test_adapter_consumes_latest_inline_lifecycle_declaration() -> None:
    payload, keys = OpenClawContextAdapter.extract_inline_lifecycle(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "[[ctxttl-inactive-turns:none]]\n[[ctxttl-turn:first]]\nfirst",
                },
                {"role": "assistant", "content": "ack"},
                {
                    "role": "user",
                    "content": (
                        "timestamp [[ctxttl-inactive-turns:first,stale:2]]\n"
                        "[[ctxttl-turn:latest]]\nlatest"
                    ),
                },
            ]
        }
    )

    assert keys == ("first", "stale:2")
    assert all(
        "ctxttl-inactive-turns" not in message.get("content", "") for message in payload["messages"]
    )
    assert "[[ctxttl-turn:latest]]" in payload["messages"][-1]["content"]


async def test_adapter_isolates_sessions_accounts_and_agents() -> None:
    adapter = OpenClawContextAdapter(RecordingContextPort())
    payload: dict[str, Any] = {"messages": []}

    async def session_id(**overrides: str) -> str:
        values = {
            "agent_id": "main",
            "session_key": "conversation-1",
            "channel_id": "weixin",
            "account_id": "account-1",
        }
        values.update(overrides)
        return (await compile_request(adapter, payload, OpenClawTurnContext(**values))).session_id

    baseline = await session_id()
    assert await session_id(session_key="conversation-2") != baseline
    assert await session_id(account_id="account-2") != baseline
    assert await session_id(agent_id="secondary") != baseline


async def test_adapter_keeps_optional_scopes_absent_instead_of_inventing_them() -> None:
    request = await compile_request(
        OpenClawContextAdapter(RecordingContextPort()),
        {"messages": []},
        OpenClawTurnContext(agent_id="main", session_key="conversation-1"),
    )

    assert request.user_id is None
    assert request.task_id is None
    assert request.turn_id is None
    assert request.request_id is None


@pytest.mark.parametrize("field", ["agent_id", "session_key", "channel_id", "turn_key"])
def test_turn_context_rejects_blank_coordinates(field: str) -> None:
    values = {"agent_id": "main", "session_key": "conversation-1"}
    values[field] = "  "

    with pytest.raises(ValueError, match=field):
        OpenClawTurnContext(**values)
