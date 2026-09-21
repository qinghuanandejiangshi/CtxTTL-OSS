import pytest

from ctxttl.config import MissingSessionBehavior, Settings
from ctxttl.identity import (
    InvalidIdentity,
    MissingSessionIdentity,
    RequestIdentity,
    ResolutionAction,
    reachable_owner_keys,
    resolve_identity,
    resolve_request_id,
)
from ctxttl.models import ContextOwner, ContextScope


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_identity_headers_are_case_insensitive() -> None:
    resolution = resolve_identity(
        {
            "x-ctxttl-session-id": "session-1",
            "X-CTxTTL-USER-ID": "user-1",
            "X-CtxTTL-Task-ID": "task-1",
            "X-CtxTTL-Agent-ID": "researcher",
            "X-CtxTTL-Project-ID": "project-1",
            "X-CtxTTL-Turn-ID": "turn-1",
        },
        settings=settings(),
    )

    assert resolution.action == ResolutionAction.COMPILE
    assert resolution.identity is not None
    assert resolution.identity.session_id == "session-1"
    assert resolution.identity.user_id == "user-1"
    assert resolution.identity.task_id == "task-1"
    assert resolution.identity.agent_id == "researcher"
    assert resolution.identity.project_id == "project-1"
    assert resolution.identity.turn_id == "turn-1"


def test_multi_agent_coordinates_share_project_and_task_but_isolate_agent_scope() -> None:
    first = RequestIdentity(
        session_id="session-a",
        user_id="user-1",
        project_id="project-1",
        task_id="task-1",
        agent_id="researcher",
    )
    second = RequestIdentity(
        session_id="session-b",
        user_id="user-1",
        project_id="project-1",
        task_id="task-1",
        agent_id="coder",
    )
    first_keys = set(reachable_owner_keys(first))
    second_keys = set(reachable_owner_keys(second))
    first_owner = ContextOwner(**first.model_dump(exclude={"turn_id"}))
    second_owner = ContextOwner(**second.model_dump(exclude={"turn_id"}))

    assert first_owner.key_for(ContextScope.TASK) in second_keys
    assert first_owner.key_for(ContextScope.PROJECT) in second_keys
    assert first_owner.key_for(ContextScope.USER) in second_keys
    assert first_owner.key_for(ContextScope.AGENT) not in second_keys
    assert first_owner.key_for(ContextScope.SESSION) not in second_keys
    assert second_owner.key_for(ContextScope.AGENT) not in first_keys


def test_legacy_task_owner_key_remains_stable_without_v2_coordinates() -> None:
    owner = ContextOwner(session_id="session-1", user_id="user-1", task_id="task-1")

    assert owner.key_for(ContextScope.TASK) == '["task","user-1","task-1"]'


def test_explicit_user_header_wins_over_request_user() -> None:
    resolution = resolve_identity(
        {
            "X-CtxTTL-Session-ID": "session-1",
            "X-CtxTTL-User-ID": "header-user",
        },
        request_user="body-user",
        settings=settings(),
    )

    assert resolution.identity is not None
    assert resolution.identity.user_id == "header-user"


def test_missing_session_rejects_by_default() -> None:
    with pytest.raises(MissingSessionIdentity, match="X-CtxTTL-Session-ID"):
        resolve_identity({}, settings=settings())


def test_missing_session_can_be_explicit_passthrough() -> None:
    resolution = resolve_identity(
        {},
        settings=settings(missing_session_behavior=MissingSessionBehavior.PASSTHROUGH),
    )

    assert resolution.action == ResolutionAction.PASSTHROUGH
    assert resolution.identity is None
    assert resolution.reason == "missing_session_identity"


def test_request_user_is_not_used_as_session_identity() -> None:
    with pytest.raises(MissingSessionIdentity):
        resolve_identity({}, request_user="user-1", settings=settings())


def test_unsafe_identity_is_rejected() -> None:
    with pytest.raises(InvalidIdentity):
        resolve_identity(
            {"X-CtxTTL-Session-ID": "session with spaces"},
            settings=settings(),
        )


def test_request_id_uses_valid_client_key_or_generates_one() -> None:
    assert (
        resolve_request_id(
            {"x-ctxttl-request-id": "client-request-1"},
            settings=settings(),
        )
        == "client-request-1"
    )
    assert resolve_request_id({}, settings=settings()).startswith("req_")


def test_unsafe_request_id_is_rejected() -> None:
    with pytest.raises(InvalidIdentity):
        resolve_request_id(
            {"X-CtxTTL-Request-ID": "unsafe request"},
            settings=settings(),
        )
