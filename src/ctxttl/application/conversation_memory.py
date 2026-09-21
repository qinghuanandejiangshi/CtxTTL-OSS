"""Conversation archive and historical retrieval orchestration."""

import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from ctxttl.archive import (
    ArchiveSearchHit,
    ConversationArchive,
    ConversationEntry,
    MessageDirection,
)
from ctxttl.identity import RequestIdentity
from ctxttl.models import (
    Authority,
    ContextItem,
    ContextKind,
    ContextOwner,
    ContextScope,
    Retention,
    SourceRef,
)
from ctxttl.models.context import utc_now
from ctxttl.observability import CompilationTrace


class ConversationMemoryService:
    """Archive raw input and expose bounded retrieval as low-authority Context IR."""

    def __init__(
        self,
        archive: ConversationArchive,
        *,
        archive_enabled: bool = True,
        archive_retention_days: int | None = None,
        retrieval_enabled: bool = True,
        retrieval_limit: int = 2,
        reference_retrieval_limit: int = 6,
    ) -> None:
        if retrieval_limit < 1 or reference_retrieval_limit < retrieval_limit:
            raise ValueError("conversation retrieval limits are invalid")
        self._archive = archive
        self._archive_enabled = archive_enabled
        self._archive_retention_days = archive_retention_days
        self._retrieval_enabled = retrieval_enabled
        self._retrieval_limit = retrieval_limit
        self._reference_retrieval_limit = reference_retrieval_limit

    async def retrieve(
        self,
        raw_messages: list[Any],
        identity: RequestIdentity,
        owner_keys: tuple[str, ...],
    ) -> list[ContextItem]:
        if not self._retrieval_enabled:
            return []
        query = self._last_user_text(raw_messages)
        if not query:
            return []
        limit = (
            self._reference_retrieval_limit
            if self._has_history_reference(query)
            else self._retrieval_limit
        )
        hits = await self._archive.search(query, owner_keys, limit=limit)
        current_messages = [
            dict(message) for message in raw_messages if isinstance(message, Mapping)
        ]
        distinct_hits = [hit for hit in hits if hit.entry.message not in current_messages]
        return [
            self._retrieved_item(hit, identity, position)
            for position, hit in enumerate(distinct_hits)
        ]

    async def archive(
        self,
        messages: list[dict[str, Any]],
        identity: RequestIdentity,
        trace: CompilationTrace,
        *,
        request_id: str | None = None,
        direction: MessageDirection = MessageDirection.INPUT,
    ) -> None:
        if not self._archive_enabled:
            return
        if self._archive_retention_days is not None:
            await self._archive.prune(
                before=utc_now() - timedelta(days=self._archive_retention_days)
            )
        owner = ContextOwner(
            session_id=identity.session_id,
            user_id=identity.user_id,
            task_id=identity.task_id,
            agent_id=identity.agent_id,
            project_id=identity.project_id,
        )
        entries = [
            ConversationEntry.create(
                owner=owner,
                message=message,
                source_trace_id=trace.id,
                request_id=request_id or trace.id,
                direction=direction,
                created_at=trace.created_at,
                position=position,
            )
            for position, message in enumerate(messages)
        ]
        await self._archive.append(entries)

    @staticmethod
    def _retrieved_item(
        hit: ArchiveSearchHit,
        identity: RequestIdentity,
        position: int,
    ) -> ContextItem:
        entry = hit.entry
        if identity.task_id is not None and entry.owner.task_id == identity.task_id:
            scope = ContextScope.TASK
        elif identity.agent_id is not None and entry.owner.agent_id == identity.agent_id:
            scope = ContextScope.AGENT
        elif identity.project_id is not None and entry.owner.project_id == identity.project_id:
            scope = ContextScope.PROJECT
        elif entry.owner.session_id == identity.session_id:
            scope = ContextScope.SESSION
        else:
            scope = ContextScope.USER
        return ContextItem(
            id=f"ctx_{entry.id}",
            kind=ContextKind.MESSAGE,
            scope=scope,
            retention=Retention.ARCHIVED,
            authority=Authority.RETRIEVED_SOURCE,
            subject=f"archive.message.{entry.id}",
            value=entry.message,
            priority=max(0.35, 0.65 - position * 0.05),
            confidence=0.7,
            created_at=entry.created_at,
            owner=entry.owner,
            source=SourceRef(
                session_id=entry.owner.session_id,
                timestamp=entry.created_at,
            ),
            metadata={
                "archive_entry_id": entry.id,
                "source_trace_id": entry.source_trace_id,
                "retrieval_position": position,
                "bm25_rank": hit.rank,
            },
        )

    @classmethod
    def _last_user_text(cls, messages: list[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, Mapping) and message.get("role") == "user":
                return " ".join(cls._text_values(message.get("content"))).strip()
        return ""

    @classmethod
    def _text_values(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [text for child in value for text in cls._text_values(child)]
        if isinstance(value, Mapping):
            return [text for child in value.values() for text in cls._text_values(child)]
        return []

    @staticmethod
    def _has_history_reference(query: str) -> bool:
        return bool(
            re.search(
                r"(?:刚才|之前|先前|上次|还记得|已经说过|提到过|"
                r"\bearlier\b|\bprevious(?:ly)?\b|\bbefore\b|\blast time\b|"
                r"\bremember\b|\bsaid\b|\bmentioned\b)",
                query,
                flags=re.IGNORECASE,
            )
        )
