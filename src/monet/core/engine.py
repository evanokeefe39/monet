"""Agent execution engine — single-task execution lifecycle.

Boundary:
  Worker (_loop.py) — claim loop, semaphore, heartbeat, progress drain
    |
  Engine (engine.py) — execute_task(): context setup, handler call, timeout,
      complete/fail
    |
  Decorator (decorator.py) — @agent SDK: hooks, params, signals, artifacts,
      lifecycle events

Design decision: lifecycle events (agent_started, agent_completed, agent_failed,
hitl_cause) remain in the decorator's wrapper(), not here. They are agent-level
semantic events requiring knowledge of agent exception taxonomy (NeedsHumanReview,
EscalationRequired). The decorator catches all exceptions and always returns
AgentResult — engine never sees exceptions from the handler.

Import constraint: MUST NOT import from monet.orchestration, monet.worker, or langgraph.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from monet.core.registry import LocalRegistry
    from monet.events import TaskRecord
    from monet.progress._protocol import ProgressWriter
    from monet.queue import TaskQueue

logger = logging.getLogger("monet.engine")
_tracer = trace.get_tracer("monet.worker")


async def _record_lifecycle(
    run_id: str,
    task_id: str,
    agent_id: str,
    event_type_str: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Record a lifecycle ProgressEvent. Best-effort — never raises."""
    try:
        import time as _time

        from monet.core.stubs import _progress_writer_cv

        pw = _progress_writer_cv.get()
        if pw is None:
            return
        from monet.events import EventType, ProgressEvent

        event: ProgressEvent = {
            "event_id": 0,
            "run_id": run_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "event_type": EventType(event_type_str),
            "timestamp_ms": int(_time.time() * 1000),
        }
        if payload:
            event["payload"] = payload
        await pw.record(run_id, event)
    except Exception:
        pass


async def execute_task(
    record: TaskRecord,
    registry: LocalRegistry,
    queue: TaskQueue,
    *,
    publisher: Callable[[dict[str, Any]], None] | None = None,
    writer: ProgressWriter | None = None,
    pool: str = "local",
    task_timeout: float = 300.0,
    on_before_complete: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Execute a single task record: set context, run handler, complete or fail.

    Args:
        record: The task to execute.
        registry: Handler registry to look up the agent handler.
        queue: Queue to call complete() or fail() on.
        publisher: Optional sync callable receiving progress event dicts.
            Forwarded to _progress_publisher ContextVar.
        writer: Optional ProgressWriter forwarded to _progress_writer_cv ContextVar.
        pool: Pool name, recorded on the OTel span.
        task_timeout: Max seconds the handler may run before timeout failure.
        on_before_complete: Async callback invoked before every queue.complete()
            or queue.fail() call. Used by the worker to flush the progress drain
            BEFORE completing — completing terminates progress subscriptions.
    """
    from monet.core.stubs import (
        _current_task_id,
        _progress_publisher,
        _progress_writer_cv,
    )

    task_id = record["task_id"]
    agent_id = record["agent_id"]
    command = record["command"]

    token = _progress_publisher.set(publisher)
    writer_token = _progress_writer_cv.set(writer)
    task_id_token = _current_task_id.set(task_id)

    async def _before_complete() -> None:
        if on_before_complete is not None:
            await on_before_complete()

    try:
        with _tracer.start_as_current_span(
            f"worker.execute.{agent_id}.{command}",
            attributes={
                "agent.id": agent_id,
                "agent.command": command,
                "worker.pool": pool,
                "task.id": task_id,
            },
        ):
            handler = registry.lookup(agent_id, command)
            if handler is None:
                logger.warning(
                    "worker: no handler for %s/%s (task %s)",
                    agent_id,
                    command,
                    task_id,
                )
                await queue.fail(
                    task_id,
                    f"No handler for {agent_id}/{command} in worker registry",
                )
                return
            logger.info(
                "worker: executing %s/%s task=%s pool=%s",
                agent_id,
                command,
                task_id,
                pool,
            )
            try:
                result = await asyncio.wait_for(
                    handler(record["context"]),
                    timeout=task_timeout,
                )
                # Flush progress BEFORE completing — completing triggers
                # wait_completion cleanup which terminates subscriptions.
                await _before_complete()
                await queue.complete(task_id, result)
                logger.info(
                    "worker: completed %s/%s task=%s success=%s",
                    agent_id,
                    command,
                    task_id,
                    getattr(result, "success", True),
                )
            except TimeoutError:
                logger.warning(
                    "worker: task %s timed out after %ss (%s/%s)",
                    task_id,
                    task_timeout,
                    agent_id,
                    command,
                )
                await _before_complete()
                await queue.fail(
                    task_id,
                    f"Task execution timed out after {task_timeout}s",
                )
            except Exception as exc:
                logger.exception(
                    "Worker: unhandled exception executing %s/%s",
                    agent_id,
                    command,
                )
                await _before_complete()
                await queue.fail(task_id, f"{type(exc).__name__}: {exc}")
    finally:
        _progress_publisher.reset(token)
        _progress_writer_cv.reset(writer_token)
        _current_task_id.reset(task_id_token)
