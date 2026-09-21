"""Archive contracts owned by the domain boundary."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from ctxttl.archive.models import ArchiveSearchHit, ConversationEntry


class ArchiveConflict(RuntimeError):
    """Raised when one request occurrence is reused with different content."""


class ConversationArchive(Protocol):
    """Persist and retrieve raw messages without depending on a provider or database."""

    async def initialize(self) -> None: ...

    async def append(self, entries: Sequence[ConversationEntry]) -> list[ConversationEntry]: ...

    async def search(
        self,
        query: str,
        owner_keys: Sequence[str],
        *,
        limit: int = 10,
    ) -> list[ArchiveSearchHit]: ...

    async def prune(self, *, before: datetime) -> int: ...

    async def aclose(self) -> None: ...
