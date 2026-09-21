"""Ownership-safe query, lifecycle API, and automatic expiry tests."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ctxttl.api import create_app
from ctxttl.application import ContextManagementService, ContextStateManager
from ctxttl.application.ports import BufferedProviderResponse, StreamingProviderResponse
from ctxttl.config import Settings
from ctxttl.identity import RequestIdentity
from ctxttl.models import (
    Authority,
    ContextItem,
    ContextKind,
    ContextOwner,
    ContextScope,
    ContextStatus,
    Retention,
    SourceRef,
)
from ctxttl.storage import SQLiteContextStateStore


class UnusedTransport:
    async def complete(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> BufferedProviderResponse:
        raise AssertionError("context management must not call the provider")

    async def stream(
        self,
        payload: Mapping[str, Any],
        request_headers: Mapping[str, str],
    ) -> StreamingProviderResponse:
        raise AssertionError("context management must not call the provider")


def context_item(
    *,
    session_id: str = "session-1",
    subject: str = "project.database",
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> ContextItem:
    timestamp = created_at or datetime.now(UTC)
    return ContextItem(
        kind=ContextKind.FACT,
        scope=ContextScope.SESSION,
        retention=Retention.LEASED if expires_at is not None else Retention.PERSISTENT,
        authority=Authority.EXPLICIT_USER,
        subject=subject,
        value="PostgreSQL",
        created_at=timestamp,
        expires_at=expires_at,
        owner=ContextOwner(session_id=session_id),
        source=SourceRef(session_id=session_id, timestamp=timestamp),
    )


async def test_due_context_is_expired_before_active_state_is_returned() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    await store.initialize()
    try:
        now = datetime.now(UTC)
        asserted = await ContextStateManager(store).assert_item(
            context_item(
                created_at=now - timedelta(days=2),
                expires_at=now - timedelta(days=1),
            )
        )
        assert asserted.item is not None

        active = await ContextManagementService(store).list_active(
            RequestIdentity(session_id="session-1")
        )
        stored = await store.get_item(asserted.item.id)

        assert active == []
        assert stored is not None and stored.status == ContextStatus.EXPIRED
        assert [event.event_type.value for event in await store.list_events()] == [
            "assert",
            "expire",
        ]
    finally:
        await store.aclose()


async def test_context_management_api_lists_gets_and_retracts_reachable_state() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    settings = Settings(database_url="sqlite:///:memory:", _env_file=None)
    app = create_app(settings, UnusedTransport(), store, store, store)
    await store.initialize()
    headers = {"X-CtxTTL-Session-ID": "session-1"}
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            asserted = await client.post(
                "/v1/context/items",
                headers=headers,
                json={
                    "kind": "decision",
                    "scope": "session",
                    "subject": "project.database",
                    "value": "PostgreSQL",
                    "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                },
            )
            context_id = asserted.json()["item"]["id"]
            listed = await client.get(
                "/v1/context/items",
                headers=headers,
                params={"status": "active"},
            )
            fetched = await client.get(f"/v1/context/items/{context_id}", headers=headers)
            hidden = await client.get(
                f"/v1/context/items/{context_id}",
                headers={"X-CtxTTL-Session-ID": "other-session"},
            )
            retracted = await client.post(
                f"/v1/context/items/{context_id}/retract",
                headers=headers,
                json={"reason": "decision withdrawn"},
            )
            active_after = await client.get(
                "/v1/context/items",
                headers=headers,
                params={"status": "active"},
            )

        assert asserted.status_code == 201
        assert asserted.json()["item"]["retention"] == "leased"
        assert [item["id"] for item in listed.json()["items"]] == [context_id]
        assert fetched.json()["item"]["id"] == context_id
        assert hidden.status_code == 404
        assert retracted.status_code == 200
        assert retracted.json()["event"]["event_type"] == "retract"
        assert active_after.json()["items"] == []
    finally:
        await store.aclose()


async def test_expire_endpoint_ends_active_lifecycle() -> None:
    store = SQLiteContextStateStore("sqlite:///:memory:")
    settings = Settings(database_url="sqlite:///:memory:", _env_file=None)
    app = create_app(settings, UnusedTransport(), store, store, store)
    await store.initialize()
    headers = {"X-CtxTTL-Session-ID": "session-1"}
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            asserted = await client.post(
                "/v1/context/items",
                headers=headers,
                json={
                    "kind": "task_state",
                    "scope": "session",
                    "subject": "phase",
                    "value": "complete",
                },
            )
            context_id = asserted.json()["item"]["id"]
            expired = await client.post(
                f"/v1/context/items/{context_id}/expire",
                headers=headers,
                json={"reason": "phase closed"},
            )
            fetched = await client.get(f"/v1/context/items/{context_id}", headers=headers)

        assert expired.status_code == 200
        assert expired.json()["event"]["event_type"] == "expire"
        assert fetched.json()["item"]["status"] == "expired"
    finally:
        await store.aclose()
