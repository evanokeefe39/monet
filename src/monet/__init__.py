"""monet — multi-agent orchestration SDK."""

from .core.agent_manifest import AgentManifestHandle, get_agent_manifest
from .core.artifacts import ArtifactStoreHandle, get_artifacts
from .core.context import get_run_context, get_run_logger
from .core.context_resolver import resolve_context
from .core.decorator import agent
from .core.hooks import GraphHookRegistry, HookRegistry, on_hook
from .core.stubs import emit_progress, emit_signal, write_artifact
from .exceptions import EscalationRequired, NeedsHumanReview, SemanticError
from .handlers import log_handler, webhook_handler
from .signals import AUDIT, BLOCKING, INFORMATIONAL, RECOVERABLE, ROUTING
from .streams import AgentStream
from .types import (
    AgentMeta,
    AgentResult,
    AgentRunContext,
    ArtifactPointer,
    Signal,
    SignalType,
    find_artifact,
)

__all__ = [
    "AUDIT",
    "BLOCKING",
    "INFORMATIONAL",
    "RECOVERABLE",
    "ROUTING",
    "AgentManifestHandle",
    "AgentMeta",
    "AgentResult",
    "AgentRunContext",
    "AgentStream",
    "ArtifactPointer",
    "ArtifactStoreHandle",
    "EscalationRequired",
    "GraphHookRegistry",
    "HookRegistry",
    "NeedsHumanReview",
    "SemanticError",
    "Signal",
    "SignalType",
    "agent",
    "emit_progress",
    "emit_signal",
    "find_artifact",
    "get_agent_manifest",
    "get_artifacts",
    "get_run_context",
    "get_run_logger",
    "log_handler",
    "on_hook",
    "resolve_context",
    "webhook_handler",
    "write_artifact",
]
