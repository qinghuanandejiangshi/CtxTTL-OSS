"""Application use cases for CtxTTL."""

from ctxttl.application.agent_context import (
    AgentContextPort,
    AgentContextRequest,
    LifecycleAwareAgentContext,
)
from ctxttl.application.chat_proxy import ChatCompletionProxy, PreparedChatRequest, ProxyMode
from ctxttl.application.context_compilation import ChatContextCompilationService
from ctxttl.application.context_management import ContextManagementService, LifecycleInput
from ctxttl.application.context_state import ContextStateManager
from ctxttl.application.conversation_memory import ConversationMemoryService
from ctxttl.application.responses_compilation import ResponsesContextCompilationService
from ctxttl.application.responses_protocol import ResponsesProtocolAdapter
from ctxttl.application.structured_ingestion import (
    StructuredContextIngestionService,
    StructuredContextInput,
    StructuredIngestionError,
)
from ctxttl.application.trace_inspection import (
    ReplayUnavailable,
    TraceInspectionService,
    TraceNotFound,
    TraceReplay,
)
from ctxttl.application.transcript_lifecycle import (
    InactiveTranscriptTurn,
    LifecycleFilterResult,
    TranscriptLifecycleFilter,
)

__all__ = [
    "AgentContextPort",
    "AgentContextRequest",
    "ChatCompletionProxy",
    "ChatContextCompilationService",
    "ContextStateManager",
    "ContextManagementService",
    "ConversationMemoryService",
    "PreparedChatRequest",
    "ProxyMode",
    "LifecycleInput",
    "LifecycleAwareAgentContext",
    "StructuredContextIngestionService",
    "StructuredContextInput",
    "StructuredIngestionError",
    "ReplayUnavailable",
    "ResponsesContextCompilationService",
    "ResponsesProtocolAdapter",
    "TraceInspectionService",
    "TraceNotFound",
    "TraceReplay",
    "InactiveTranscriptTurn",
    "LifecycleFilterResult",
    "TranscriptLifecycleFilter",
]
