"""Transport-agnostic agent invocation.

Dispatches via the configured task queue. Transport (local execution,
HTTP forwarding, etc.) is a worker concern — ``invoke_agent`` only
knows about the queue.
"""

from __future__ import annotations

import os
import secrets
import uuid
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from monet.queue import TaskQueue
    from monet.types import AgentResult, AgentRunContext

# Default timeout for queue poll (seconds). Override via MONET_AGENT_TIMEOUT.
_DEFAULT_TIMEOUT = 600.0

_RESERVED_FIELDS = {"task", "context", "command", "trace_id", "run_id", "skills"}

# Module-level queue — set via configure_queue() or bootstrap().
_task_queue: TaskQueue | None = None


def configure_queue(queue: TaskQueue | None) -> None:
    """Set or clear the task queue used by ``invoke_agent``.

    In monolith mode, ``bootstrap()`` creates an in-memory queue and
    starts a background worker. In distributed mode, the queue connects
    to an external broker (Redis, etc.).
    """
    global _task_queue
    _task_queue = queue


def get_queue() -> TaskQueue | None:
    """Return the currently configured queue, or None."""
    return _task_queue


def _get_timeout() -> float:
    raw = os.environ.get("MONET_AGENT_TIMEOUT")
    if not raw:
        return _DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT


def _generate_trace_id() -> str:
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


async def invoke_agent(
    agent_id: str,
    command: str = "fast",
    task: str = "",
    context: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    skills: list[str] | None = None,
    **kwargs: Any,
) -> AgentResult:
    """Invoke an agent by ID and command via the task queue.

    Standard envelope fields are explicit parameters. Agent-specific
    parameters pass as **kwargs but must not shadow reserved fields.
    Routing is always driven by AgentResult.signals, never by kwargs values.
    """
    conflicts = _RESERVED_FIELDS & set(kwargs)
    if conflicts:
        msg = (
            f"invoke_agent() kwargs conflict with reserved fields: {conflicts}. "
            "Pass these as explicit parameters."
        )
        raise ValueError(msg)

    if _task_queue is None:
        msg = (
            "No task queue configured. "
            "Call configure_queue() or bootstrap() before invoking agents."
        )
        raise RuntimeError(msg)

    resolved_run_id = run_id or str(uuid.uuid4())
    resolved_trace_id = trace_id or _generate_trace_id()

    ctx: AgentRunContext = {
        "task": task,
        "context": context or [],
        "command": command,
        "trace_id": resolved_trace_id,
        "run_id": resolved_run_id,
        "agent_id": agent_id,
        "skills": skills or [],
    }

    tracer = trace.get_tracer("monet.orchestration")
    with tracer.start_as_current_span(
        f"agent.{agent_id}.{command}",
        attributes={
            "agent.id": agent_id,
            "agent.command": command,
            "monet.run_id": resolved_run_id,
        },
    ) as span:
        task_id = await _task_queue.enqueue(agent_id, command, ctx)
        result = await _task_queue.poll_result(task_id, timeout=_get_timeout())
        span.set_attribute("agent.success", result.success)
        span.set_attribute("agent.signal_count", len(result.signals))
        return result
