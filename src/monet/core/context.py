"""Agent context system — ContextVar-based runtime context.

The decorator sets AgentRunContext before the function executes.
Any code inside can access it via get_run_context() without parameter passing.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.types import AgentRunContext

_agent_context: ContextVar[AgentRunContext] = ContextVar("_agent_context")

# Sentinel for detecting "outside decorator" state
_OUTSIDE_DECORATOR: AgentRunContext = {
    "task": "",
    "context": [],
    "command": "",
    "trace_id": "",
    "run_id": "",
    "agent_id": "",
    "skills": [],
}


def get_run_context() -> AgentRunContext:
    """Return the current AgentRunContext.

    Returns a safe default (empty context) outside the decorator,
    so functions remain testable without orchestration infrastructure.
    """
    return _agent_context.get(_OUTSIDE_DECORATOR)


def get_run_logger() -> logging.Logger:
    """Return a structured logger pre-populated with agent context fields.

    Returns a no-op logger outside the decorator.
    """
    ctx = get_run_context()
    if ctx is _OUTSIDE_DECORATOR:
        return logging.getLogger("monet.agent.noop")

    logger = logging.getLogger(f"monet.agent.{ctx['agent_id']}")
    # Use a LoggerAdapter to inject context fields into every log record
    adapter = logging.LoggerAdapter(
        logger,
        {
            "trace_id": ctx["trace_id"],
            "run_id": ctx["run_id"],
            "agent_id": ctx["agent_id"],
            "command": ctx["command"],
        },
    )
    return adapter  # type: ignore[return-value]
