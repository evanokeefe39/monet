"""monet — a multi-agent orchestration SDK."""

from ._context import get_run_context, get_run_logger
from ._decorator import agent
from ._events import handle_agent_event
from ._stubs import emit_progress, emit_signal, set_catalogue_client, write_artifact
from ._types import AgentResult, AgentRunContext, ArtifactPointer, Signal, SignalType
from .exceptions import EscalationRequired, NeedsHumanReview, SemanticError

__all__ = [
    "AgentResult",
    "AgentRunContext",
    "ArtifactPointer",
    "EscalationRequired",
    "NeedsHumanReview",
    "SemanticError",
    "Signal",
    "SignalType",
    "agent",
    "emit_progress",
    "emit_signal",
    "get_run_context",
    "get_run_logger",
    "handle_agent_event",
    "set_catalogue_client",
    "write_artifact",
]
