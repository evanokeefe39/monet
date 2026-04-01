"""Ad hoc types for the transport spike.

These are throwaway implementations to validate the transport seam.
The real SDK types will be built in Step 2.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentSignals:
    needs_human_review: bool = False
    review_reason: str | None = None
    escalation_requested: bool = False
    escalation_reason: str | None = None
    semantic_error: dict[str, str] | None = None


@dataclass(frozen=True)
class AgentResult:
    success: bool
    output: str
    signals: AgentSignals = field(default_factory=AgentSignals)
    trace_id: str = ""
    run_id: str = ""


@dataclass
class AgentRunContext:
    task: str = ""
    command: str = "fast"
    effort: str | None = None
    trace_id: str = ""
    run_id: str = ""
    agent_id: str = ""


@dataclass
class InputEnvelope:
    task: str
    command: str = "fast"
    effort: str | None = None
    trace_id: str = ""
    run_id: str = ""


@dataclass
class LocalDescriptor:
    """Descriptor pointing to a local callable."""

    agent_id: str
    callable_ref: Any  # The decorated function


@dataclass
class HttpDescriptor:
    """Descriptor pointing to a remote HTTP endpoint."""

    agent_id: str
    endpoint: str  # e.g. "http://localhost:8001/agents/researcher/fast"


AgentDescriptor = LocalDescriptor | HttpDescriptor

_agent_context: ContextVar[AgentRunContext] = ContextVar("_agent_context")


def get_run_context() -> AgentRunContext:
    """Get current agent context. Returns safe default outside decorator."""
    return _agent_context.get(AgentRunContext())
