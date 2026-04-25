"""Transport-agnostic agent invocation.

Dispatches via the configured task queue. Transport (local execution,
cloud dispatch, etc.) is a worker concern — ``invoke_agent`` only
knows about the queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

from monet.config import OrchestrationConfig
from monet.events import TASK_RECORD_SCHEMA_VERSION, TaskStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from monet.events import TaskRecord
    from monet.queue import TaskQueue
    from monet.server._capabilities import CapabilityIndex
    from monet.types import AgentResult, AgentRunContext

_log = logging.getLogger("monet.orchestration")

_RESERVED_FIELDS = {"task", "context", "command", "trace_id", "run_id", "skills"}

# Lifecycle status conventions — colon prefix distinguishes from freeform
# agent-authored statuses (e.g. "searching with Exa").
AGENT_STARTED_STATUS = "agent:started"
AGENT_COMPLETED_STATUS = "agent:completed"
AGENT_FAILED_STATUS = "agent:failed"

# Module-level queue — set via configure_queue() at server boot.
_task_queue: TaskQueue | None = None

# Module-level capability index — set via configure_capability_index() at
# server boot. Used by ``invoke_agent`` for split-fleet pool routing
# when the local registry does not own the capability.
_capability_index: CapabilityIndex | None = None


def _emit_lifecycle(data: dict[str, Any]) -> None:
    """Emit a lifecycle progress event into the current LangGraph stream."""
    from monet import emit_progress

    emit_progress(data)


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


def configure_capability_index(index: CapabilityIndex | None) -> None:
    """Set or clear the capability index used for cross-pool routing.

    Called once at server boot. Workers do not call this — they route
    in-process via the local registry's ``_pool`` metadata.
    """
    global _capability_index
    _capability_index = index


def get_capability_index() -> CapabilityIndex | None:
    """Return the currently configured capability index, or None."""
    return _capability_index


def _generate_trace_id() -> str:
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


async def wait_completion(
    queue: TaskQueue, task_id: str, timeout: float
) -> AgentResult:
    """Wait for a task's completion via the protocol method.

    Raises:
        TimeoutError: if ``timeout`` seconds elapse without a result.
        KeyError: if task_id was never enqueued.
        AwaitAlreadyConsumedError: if result TTL has expired.
    """
    return await queue.await_completion(task_id, timeout)


async def invoke_agent(
    agent_id: str,
    command: str = "fast",
    task: str = "",
    context: list[dict[str, Any]] | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    skills: list[str] | None = None,
    thread_id: str | None = None,
    **kwargs: Any,
) -> AgentResult:
    """Invoke an agent by ID and command via the task queue.

    Standard envelope fields are explicit parameters. Agent-specific
    parameters pass as **kwargs but must not shadow reserved fields.
    Routing is always driven by AgentResult.signals, never by kwargs values.

    ``thread_id`` propagates into ``AgentRunContext`` so artifacts
    written by the agent carry thread provenance — the chat TUI reads
    this via ``query_recent(thread_id=...)`` to show per-thread counts.
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

    # 0. Sniff orchestration context (for in-graph invocations)
    if not thread_id:
        try:
            from langgraph.config import get_config

            cfg = get_config()
            thread_id = cfg.get("configurable", {}).get("thread_id")
        except (ImportError, LookupError, RuntimeError):
            pass

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
        "thread_id": thread_id or "",
    }

    # Pool routing: prefer the local registry (in-process ``@agent``
    # handlers carry pool on the wrapper), fall back to the server
    # ``CapabilityIndex`` (populated by worker heartbeats), fall back to
    # "local" when neither knows the capability.
    from monet.core.registry import default_registry

    pool: str | None = None
    local_handler = default_registry.lookup(agent_id, command)
    if local_handler is not None:
        pool = getattr(local_handler, "_pool", None)
    if pool is None and _capability_index is not None:
        pool = _capability_index.get_pool(agent_id, command)
    if pool is None:
        pool = "local"

    # 2. Prepare task context for worker-side rehydration
    # Ensure all orchestration metadata (thread_id, run_id) is preserved
    ctx["run_id"] = resolved_run_id

    task_id = str(uuid.uuid4())
    record: TaskRecord = {
        "schema_version": TASK_RECORD_SCHEMA_VERSION,
        "task_id": task_id,
        "agent_id": agent_id,
        "command": command,
        "pool": pool,
        "context": ctx,
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": datetime.now(UTC).isoformat(),
        "claimed_at": None,
        "completed_at": None,
    }

    tracer = trace.get_tracer("monet.orchestration")
    _log.info(
        "invoke_agent dispatch agent=%s command=%s pool=%s task_id=%s run_id=%s",
        agent_id,
        command,
        pool,
        task_id,
        resolved_run_id,
    )
    _lifecycle = {
        "agent": agent_id,
        "command": command,
        "run_id": resolved_run_id,
        "task_id": task_id,
        "thread_id": str(ctx.get("thread_id") or ""),
        "timestamp_ms": int(time.time() * 1000),
    }
    with tracer.start_as_current_span(
        f"agent.{agent_id}.{command}",
        attributes={
            "agent.id": agent_id,
            "agent.command": command,
            "monet.run_id": resolved_run_id,
            "monet.task_id": task_id,
        },
    ) as span:
        # Register the task with the queue so wait_completion has state
        # to observe. Pull pools rely on a worker's claim loop.
        await _task_queue.enqueue(record)

        # Emit started status AFTER enqueue so the task identity is established
        _emit_lifecycle({"status": AGENT_STARTED_STATUS, **_lifecycle})
        _stream_writer: Callable[[dict[str, Any]], None] | None = None
        try:
            from langgraph.config import get_stream_writer

            _stream_writer = get_stream_writer()
        except (LookupError, RuntimeError):
            pass
        progress_task = asyncio.create_task(
            _forward_progress(_task_queue, task_id, writer=_stream_writer)
        )
        try:
            timeout = OrchestrationConfig.load().agent_timeout
            result = await wait_completion(_task_queue, task_id, timeout=timeout)
            span.set_attribute("agent.success", result.success)
            span.set_attribute("agent.signal_count", len(result.signals))
            _log.info(
                "invoke_agent result agent=%s command=%s success=%s "
                "signals=%d task_id=%s",
                agent_id,
                command,
                result.success,
                len(result.signals),
                task_id,
            )
            if result.success:
                _emit_lifecycle({"status": AGENT_COMPLETED_STATUS, **_lifecycle})
            else:
                reasons = []
                for s in result.signals:
                    r = s.get("reason", "")
                    if r:
                        reasons.append(r)
                _emit_lifecycle(
                    {
                        "status": AGENT_FAILED_STATUS,
                        **_lifecycle,
                        "reasons": "; ".join(reasons),
                    }
                )
            return result
        finally:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task


async def _forward_progress(
    queue: TaskQueue,
    task_id: str,
    *,
    writer: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Forward queue progress events to the LangGraph stream writer."""
    if writer is None:
        return
    try:
        async for event in queue.subscribe_progress(task_id):
            writer(event)
    except NotImplementedError:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        _log.debug("Progress forwarding ended for task %s", task_id, exc_info=True)
