"""monet — multi-agent orchestration SDK."""

from ._catalogue import get_catalogue
from ._context import get_run_context, get_run_logger
from ._context_resolver import resolve_context
from ._decorator import agent
from ._stubs import emit_progress, emit_signal, write_artifact
from .exceptions import EscalationRequired, NeedsHumanReview, SemanticError
from .handlers import log_handler, webhook_handler
from .signals import AUDIT, BLOCKING, INFORMATIONAL, RECOVERABLE, ROUTING
from .streams import AgentStream
from .types import AgentResult, AgentRunContext, ArtifactPointer, Signal, SignalType

__all__ = [
    "AUDIT",
    "BLOCKING",
    "INFORMATIONAL",
    "RECOVERABLE",
    "ROUTING",
    "AgentResult",
    "AgentRunContext",
    "AgentStream",
    "ArtifactPointer",
    "EscalationRequired",
    "NeedsHumanReview",
    "SemanticError",
    "Signal",
    "SignalType",
    "agent",
    "emit_progress",
    "emit_signal",
    "get_catalogue",
    "get_run_context",
    "get_run_logger",
    "resolve_context",
    "log_handler",
    "webhook_handler",
    "write_artifact",
]
