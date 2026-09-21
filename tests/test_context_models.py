from datetime import datetime

import pytest
from pydantic import ValidationError

from ctxttl.models import (
    Authority,
    ContextApplicability,
    ContextEvent,
    ContextItem,
    ContextKind,
    ContextOwner,
    ContextScope,
    EventType,
    Retention,
    SourceRef,
)


def source() -> SourceRef:
    return SourceRef(session_id="session-1", turn_id="turn-1")


def owner() -> ContextOwner:
    return ContextOwner(session_id="session-1", task_id="task-1")


def test_context_item_supports_orthogonal_dimensions() -> None:
    item = ContextItem(
        kind=ContextKind.FACT,
        scope=ContextScope.TASK,
        retention=Retention.PERSISTENT,
        authority=Authority.EXPLICIT_USER,
        owner=owner(),
        subject="project.database",
        value="postgresql",
        source=source(),
    )

    assert item.id.startswith("ctx_")
    assert item.value == "postgresql"
    assert item.status.value == "active"
    assert item.applicability == ContextApplicability.SELECTIVE


def test_current_turn_context_requires_turn_scope_and_ephemeral_retention() -> None:
    with pytest.raises(ValidationError, match="turn scope"):
        ContextItem(
            kind=ContextKind.CONSTRAINT,
            scope=ContextScope.SESSION,
            retention=Retention.EPHEMERAL,
            applicability=ContextApplicability.CURRENT_TURN,
            authority=Authority.EXPLICIT_USER,
            owner=owner(),
            source=source(),
        )

    with pytest.raises(ValidationError, match="ephemeral retention"):
        ContextItem(
            kind=ContextKind.CONSTRAINT,
            scope=ContextScope.TURN,
            retention=Retention.PERSISTENT,
            applicability=ContextApplicability.CURRENT_TURN,
            authority=Authority.EXPLICIT_USER,
            owner=owner(),
            source=source(),
        )


def test_untrusted_context_cannot_escalate_itself_to_required() -> None:
    with pytest.raises(ValidationError, match="untrusted context"):
        ContextItem(
            kind=ContextKind.RAG_EVIDENCE,
            scope=ContextScope.SESSION,
            retention=Retention.PERSISTENT,
            applicability=ContextApplicability.REQUIRED,
            authority=Authority.RETRIEVED_SOURCE,
            owner=owner(),
            source=source(),
        )


def test_leased_context_requires_an_expiry() -> None:
    with pytest.raises(ValidationError, match="leased context requires"):
        ContextItem(
            kind=ContextKind.MESSAGE,
            scope=ContextScope.SESSION,
            retention=Retention.LEASED,
            authority=Authority.EXPLICIT_USER,
            owner=owner(),
            source=source(),
        )


def test_non_leased_context_rejects_ttl() -> None:
    with pytest.raises(ValidationError, match="only valid for leased"):
        ContextItem(
            kind=ContextKind.FACT,
            scope=ContextScope.SESSION,
            retention=Retention.PERSISTENT,
            authority=Authority.EXPLICIT_USER,
            owner=owner(),
            source=source(),
            ttl_turns=3,
        )


def test_context_item_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ContextItem(
            kind=ContextKind.FACT,
            scope=ContextScope.SESSION,
            retention=Retention.PERSISTENT,
            authority=Authority.EXPLICIT_USER,
            owner=owner(),
            source=source(),
            created_at=datetime(2026, 9, 7, 12, 0),
        )


def test_supersede_event_requires_a_target() -> None:
    with pytest.raises(ValidationError, match="target_context_id"):
        ContextEvent(
            event_type=EventType.SUPERSEDE,
            subject="project.database",
            scope=ContextScope.TASK,
            authority=Authority.EXPLICIT_USER,
            owner=owner(),
            source=source(),
            value="postgresql",
        )


def test_assert_event_rejects_a_target() -> None:
    with pytest.raises(ValidationError, match="cannot target"):
        ContextEvent(
            event_type=EventType.ASSERT,
            subject="project.database",
            scope=ContextScope.TASK,
            authority=Authority.EXPLICIT_USER,
            owner=owner(),
            source=source(),
            target_context_id="ctx_old",
            value="postgresql",
        )


def test_owner_keys_are_unambiguous_when_ids_contain_separators() -> None:
    first = ContextOwner(session_id="session", user_id="a:b", task_id="c")
    second = ContextOwner(session_id="session", user_id="a", task_id="b:c")

    assert first.key_for(ContextScope.TASK) != second.key_for(ContextScope.TASK)


def test_retract_event_cannot_contain_replacement_value() -> None:
    with pytest.raises(ValidationError, match="cannot carry"):
        ContextEvent(
            event_type=EventType.RETRACT,
            subject="project.database",
            scope=ContextScope.TASK,
            authority=Authority.EXPLICIT_USER,
            owner=owner(),
            source=source(),
            target_context_id="ctx_old",
            value="postgresql",
        )
