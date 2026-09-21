"""Explicit universal Agent identity and ownership resolution."""

import re
from collections.abc import Mapping
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from ctxttl.config import MissingSessionBehavior, Settings
from ctxttl.models import ContextOwner, ContextScope

_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class IdentityError(ValueError):
    """Base class for identity protocol failures."""


class MissingSessionIdentity(IdentityError):
    """Raised when compilation requires an explicit session identifier."""


class InvalidIdentity(IdentityError):
    """Raised when an identity value is unsafe or malformed."""


class ResolutionAction(StrEnum):
    COMPILE = "compile"
    PASSTHROUGH = "passthrough"


class RequestIdentity(BaseModel):
    """Resolved ownership boundaries for one model request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    user_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
    turn_id: str | None = None


class IdentityResolution(BaseModel):
    """Identity result plus the action the proxy should take."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: ResolutionAction
    identity: RequestIdentity | None = None
    reason: str | None = None


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value.strip() for key, value in headers.items()}


def _validate_identity(name: str, value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not _IDENTITY_PATTERN.fullmatch(value):
        raise InvalidIdentity(
            f"{name} must be 1-128 characters using letters, digits, '.', '_', ':' or '-'"
        )
    return value


def resolve_identity(
    headers: Mapping[str, str],
    *,
    request_user: str | None = None,
    settings: Settings | None = None,
) -> IdentityResolution:
    """Resolve explicit identity without inferring a session from message contents.

    Header values take precedence over the OpenAI-compatible ``user`` request field.
    The request field is only a user identity fallback and never a session identity.
    """

    runtime_settings = settings or Settings()
    normalized = _normalized_headers(headers)

    session_id = _validate_identity(
        "session_id", normalized.get(runtime_settings.session_header.lower())
    )
    header_user_id = normalized.get(runtime_settings.user_header.lower())
    user_id = _validate_identity("user_id", header_user_id or request_user)
    task_id = _validate_identity("task_id", normalized.get(runtime_settings.task_header.lower()))
    agent_id = _validate_identity("agent_id", normalized.get(runtime_settings.agent_header.lower()))
    project_id = _validate_identity(
        "project_id", normalized.get(runtime_settings.project_header.lower())
    )
    turn_id = _validate_identity("turn_id", normalized.get(runtime_settings.turn_header.lower()))

    if session_id is None:
        if runtime_settings.missing_session_behavior == MissingSessionBehavior.PASSTHROUGH:
            return IdentityResolution(
                action=ResolutionAction.PASSTHROUGH,
                reason="missing_session_identity",
            )
        raise MissingSessionIdentity(
            f"missing required identity header: {runtime_settings.session_header}"
        )

    return IdentityResolution(
        action=ResolutionAction.COMPILE,
        identity=RequestIdentity(
            session_id=session_id,
            user_id=user_id,
            task_id=task_id,
            agent_id=agent_id,
            project_id=project_id,
            turn_id=turn_id,
        ),
    )


def resolve_request_id(headers: Mapping[str, str], *, settings: Settings) -> str:
    """Return a validated client idempotency key or a fresh proxy request ID."""

    normalized = _normalized_headers(headers)
    request_id = _validate_identity("request_id", normalized.get(settings.request_header.lower()))
    return request_id or f"req_{uuid4().hex}"


def reachable_owner_keys(
    identity: RequestIdentity,
    *,
    turn_id: str | None = None,
) -> tuple[str, ...]:
    """Return every ownership boundary reachable by the resolved request identity."""

    owner = ContextOwner(
        session_id=identity.session_id,
        user_id=identity.user_id,
        task_id=identity.task_id,
        agent_id=identity.agent_id,
        project_id=identity.project_id,
    )
    keys: list[str] = []
    effective_turn_id = turn_id or identity.turn_id
    if effective_turn_id is not None:
        keys.append(owner.key_for(ContextScope.TURN, turn_id=effective_turn_id))
    keys.append(owner.key_for(ContextScope.SESSION))
    if identity.task_id is not None:
        keys.append(owner.key_for(ContextScope.TASK))
    if identity.agent_id is not None:
        keys.append(owner.key_for(ContextScope.AGENT))
    if identity.project_id is not None:
        keys.append(owner.key_for(ContextScope.PROJECT))
    if identity.user_id is not None:
        keys.append(owner.key_for(ContextScope.USER))
    return tuple(keys)
