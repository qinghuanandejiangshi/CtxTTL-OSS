"""Explicit structured context ingestion API tests."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

from ctxttl.api import create_app
from ctxttl.application.ports import BufferedProviderResponse, StreamingProviderResponse
from ctxttl.config import MissingSessionBehavior, Settings


class UnusedTransport:
    async def complete(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse:
        raise AssertionError("ingestion must not call the model provider")

    async def stream(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> StreamingProviderResponse:
        raise AssertionError("ingestion must not call the model provider")


@asynccontextmanager
async def ingestion_client(
    **settings_overrides: object,
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url="sqlite:///:memory:",
        _env_file=None,
        **settings_overrides,
    )
    app = create_app(settings, UnusedTransport())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client,
    ):
        yield client


async def test_explicit_fact_is_persisted_with_server_controlled_authority() -> None:
    async with ingestion_client() as client:
        response = await client.post(
            "/v1/context/items",
            headers={
                "X-CtxTTL-Session-ID": "session-1",
                "X-CtxTTL-User-ID": "user-1",
            },
            json={
                "kind": "fact",
                "scope": "user",
                "subject": "preferred.language",
                "value": "zh-CN",
            },
        )

    body = response.json()
    assert response.status_code == 201
    assert body["applied"] is True
    assert body["event"]["event_type"] == "assert"
    assert body["item"]["authority"] == "explicit_user"
    assert body["item"]["retention"] == "persistent"
    assert body["item"]["applicability"] == "selective"
    assert body["item"]["owner"]["user_id"] == "user-1"


async def test_universal_agent_and_project_scopes_require_explicit_coordinates() -> None:
    base = {
        "kind": "constraint",
        "subject": "architecture.boundary",
        "value": "provider-neutral",
    }
    async with ingestion_client() as client:
        missing_agent = await client.post(
            "/v1/context/items",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={**base, "scope": "agent"},
        )
        missing_project = await client.post(
            "/v1/context/items",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={**base, "scope": "project"},
        )
        agent = await client.post(
            "/v1/context/items",
            headers={
                "X-CtxTTL-Session-ID": "session-1",
                "X-CtxTTL-Agent-ID": "researcher",
                "X-CtxTTL-Project-ID": "project-1",
            },
            json={**base, "scope": "agent"},
        )
        project = await client.post(
            "/v1/context/items",
            headers={
                "X-CtxTTL-Session-ID": "session-1",
                "X-CtxTTL-Agent-ID": "researcher",
                "X-CtxTTL-Project-ID": "project-1",
            },
            json={**base, "scope": "project", "subject": "project.boundary"},
        )

    assert missing_agent.status_code == 400
    assert missing_agent.json()["error"]["code"] == "invalid_identity"
    assert missing_project.status_code == 400
    assert missing_project.json()["error"]["code"] == "invalid_identity"
    assert agent.status_code == 201
    assert agent.json()["item"]["owner"]["agent_id"] == "researcher"
    assert project.status_code == 201
    assert project.json()["item"]["owner"]["project_id"] == "project-1"


async def test_correction_supersedes_existing_item_atomically() -> None:
    headers = {
        "X-CtxTTL-Session-ID": "session-1",
        "X-CtxTTL-Task-ID": "task-1",
    }
    original = {
        "kind": "decision",
        "scope": "task",
        "subject": "project.database",
        "value": "SQLite",
    }
    async with ingestion_client() as client:
        first = await client.post("/v1/context/items", headers=headers, json=original)
        first_id = first.json()["item"]["id"]
        correction_headers = {**headers, "X-CtxTTL-Request-ID": "correction-1"}
        corrected = await client.post(
            "/v1/context/items",
            headers=correction_headers,
            json={
                **original,
                "value": "PostgreSQL",
                "supersedes": first_id,
                "reason": "production deployment requirement",
            },
        )
        retried = await client.post(
            "/v1/context/items",
            headers=correction_headers,
            json={
                **original,
                "value": "PostgreSQL",
                "supersedes": first_id,
                "reason": "production deployment requirement",
            },
        )

    body = corrected.json()
    assert corrected.status_code == 201
    assert body["event"]["event_type"] == "supersede"
    assert body["item"]["supersedes"] == first_id
    assert body["item"]["value"] == "PostgreSQL"
    assert retried.status_code == 201
    assert retried.json()["applied"] is False
    assert retried.json()["event"]["id"] == body["event"]["id"]


async def test_ingestion_never_uses_missing_identity_passthrough() -> None:
    async with ingestion_client(
        missing_session_behavior=MissingSessionBehavior.PASSTHROUGH
    ) as client:
        response = await client.post(
            "/v1/context/items",
            json={
                "kind": "fact",
                "scope": "session",
                "subject": "unsafe",
                "value": True,
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_identity"


async def test_ingestion_rejects_unsupported_kinds_and_missing_scope_identity() -> None:
    async with ingestion_client() as client:
        message = await client.post(
            "/v1/context/items",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={
                "kind": "message",
                "scope": "session",
                "subject": "raw.message",
                "value": "do not accept this as structured truth",
            },
        )
        missing_task = await client.post(
            "/v1/context/items",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={
                "kind": "task_state",
                "scope": "task",
                "subject": "phase",
                "value": "implementation",
            },
        )

    assert message.status_code == 422
    assert missing_task.status_code == 400
    assert missing_task.json()["error"]["code"] == "invalid_identity"


async def test_ingestion_rejects_duplicate_active_subject_without_explicit_correction() -> None:
    request = {
        "kind": "constraint",
        "scope": "session",
        "subject": "language",
        "value": "Chinese",
    }
    headers = {"X-CtxTTL-Session-ID": "session-1"}
    async with ingestion_client() as client:
        first = await client.post("/v1/context/items", headers=headers, json=request)
        duplicate = await client.post("/v1/context/items", headers=headers, json=request)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "context_conflict"


async def test_turn_scope_and_turn_lease_require_explicit_turn_identity() -> None:
    base = {
        "kind": "task_state",
        "scope": "turn",
        "subject": "current.step",
        "value": "inspect",
    }
    async with ingestion_client() as client:
        missing_turn = await client.post(
            "/v1/context/items",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json=base,
        )
        accepted = await client.post(
            "/v1/context/items",
            headers={
                "X-CtxTTL-Session-ID": "session-1",
                "X-CtxTTL-Turn-ID": "turn-1",
            },
            json=base,
        )
        leased = await client.post(
            "/v1/context/items",
            headers={
                "X-CtxTTL-Session-ID": "session-1",
                "X-CtxTTL-Turn-ID": "turn-1",
            },
            json={
                "kind": "constraint",
                "scope": "session",
                "subject": "temporary.constraint",
                "value": "brief",
                "ttl_turns": 2,
            },
        )

    assert missing_turn.status_code == 400
    assert accepted.status_code == 201
    assert accepted.json()["item"]["source"]["turn_id"] == "turn-1"
    assert accepted.json()["item"]["applicability"] == "current_turn"
    assert accepted.json()["item"]["retention"] == "ephemeral"
    assert leased.status_code == 201
    assert leased.json()["item"]["retention"] == "leased"
    assert leased.json()["item"]["ttl_turns"] == 2


async def test_required_context_is_explicitly_classified() -> None:
    async with ingestion_client() as client:
        response = await client.post(
            "/v1/context/items",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={
                "kind": "fact",
                "scope": "session",
                "applicability": "required",
                "subject": "customer.account",
                "value": "1234",
            },
        )

    assert response.status_code == 201
    assert response.json()["item"]["applicability"] == "required"


async def test_turn_scope_rejects_selective_applicability() -> None:
    async with ingestion_client() as client:
        response = await client.post(
            "/v1/context/items",
            headers={
                "X-CtxTTL-Session-ID": "session-1",
                "X-CtxTTL-Turn-ID": "turn-1",
            },
            json={
                "kind": "task_state",
                "scope": "turn",
                "applicability": "selective",
                "subject": "current.step",
                "value": "inspect",
            },
        )

    assert response.status_code == 422


async def test_ingestion_request_id_is_idempotent_and_rejects_changed_input() -> None:
    headers = {
        "X-CtxTTL-Session-ID": "session-1",
        "X-CtxTTL-Request-ID": "ingest-request-1",
    }
    assertion = {
        "kind": "fact",
        "scope": "session",
        "subject": "project.database",
        "value": "PostgreSQL",
    }
    async with ingestion_client() as client:
        first = await client.post("/v1/context/items", headers=headers, json=assertion)
        retry = await client.post("/v1/context/items", headers=headers, json=assertion)
        context_id = first.json()["item"]["id"]
        retracted = await client.post(
            f"/v1/context/items/{context_id}/retract",
            headers={"X-CtxTTL-Session-ID": "session-1"},
            json={},
        )
        late_retry = await client.post("/v1/context/items", headers=headers, json=assertion)
        conflict = await client.post(
            "/v1/context/items",
            headers=headers,
            json={**assertion, "value": "MySQL"},
        )
        listed = await client.get(
            "/v1/context/items",
            headers={"X-CtxTTL-Session-ID": "session-1"},
        )

    assert first.status_code == 201
    assert first.headers["X-CtxTTL-Request-ID"] == "ingest-request-1"
    assert retry.status_code == 201
    assert retry.json()["applied"] is False
    assert retry.json()["event"]["id"] == first.json()["event"]["id"]
    assert retry.json()["item"]["id"] == first.json()["item"]["id"]
    assert retracted.status_code == 200
    assert late_retry.status_code == 201
    assert late_retry.json()["item"]["status"] == "active"
    assert late_retry.json()["event"]["id"] == first.json()["event"]["id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "request_conflict"
    assert [item["id"] for item in listed.json()["items"]] == [first.json()["item"]["id"]]
