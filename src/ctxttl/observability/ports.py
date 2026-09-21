"""Persistence boundary for compilation traces."""

from typing import Protocol, runtime_checkable

from ctxttl.observability.models import CompilationTrace, ExecutionTrace


class TraceConflict(RuntimeError):
    """Raised when a trace ID is reused with different content."""


class CompilationTraceStore(Protocol):
    async def record_trace(self, trace: CompilationTrace) -> None: ...

    async def get_trace(self, trace_id: str) -> CompilationTrace | None: ...

    async def list_traces(
        self,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[CompilationTrace]: ...


@runtime_checkable
class ExecutionTraceStore(Protocol):
    """Persistence boundary for provider execution timings."""

    async def record_execution(self, execution: ExecutionTrace) -> None: ...

    async def get_execution(self, trace_id: str) -> ExecutionTrace | None: ...
