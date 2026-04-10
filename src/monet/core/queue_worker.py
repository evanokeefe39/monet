"""Queue worker — claims and executes tasks concurrently.

Follows the Prefect model: one worker per pool, claims any task in that
pool, looks up handler in local registry. Spawns concurrent asyncio.Tasks
with a configurable concurrency limit.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from monet.core.registry import AgentRegistry
    from monet.queue import TaskQueue, TaskRecord

logger = logging.getLogger("monet.worker")
_tracer = trace.get_tracer("monet.worker")

# How long to sleep between claim attempts when the pool queue is empty.
_POLL_INTERVAL = 0.1

# Graceful shutdown: max seconds to wait for in-flight tasks.
_SHUTDOWN_TIMEOUT = 30.0


async def run_worker(
    queue: TaskQueue,
    registry: AgentRegistry | None = None,
    pool: str = "local",
    max_concurrency: int = 10,
) -> None:
    """Poll queue by pool, execute concurrently via registry.

    Runs until the current ``asyncio.Task`` is cancelled. On cancellation,
    waits up to ``_SHUTDOWN_TIMEOUT`` seconds for in-flight tasks to complete.

    Args:
        queue: The task queue to poll.
        registry: Handler registry for local execution. Defaults to the
            global registry populated by ``@agent`` decorators.
        pool: Pool name this worker serves. Claims only tasks in this pool.
        max_concurrency: Max concurrent task executions. Default 10.
    """
    if registry is None:
        from monet.core.registry import default_registry

        registry = default_registry
    sem = asyncio.Semaphore(max_concurrency)
    in_flight: set[asyncio.Task[None]] = set()

    async def _execute(record: TaskRecord) -> None:
        from monet.queue import TaskStatus

        task_id = record["task_id"]
        agent_id = record["agent_id"]
        command = record["command"]

        async with sem:
            # Check if task was cancelled while waiting for semaphore
            if record.get("status") == TaskStatus.CANCELLED:
                return

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
                    await queue.fail(
                        task_id,
                        f"No handler for {agent_id}/{command} in worker registry",
                    )
                    return
                try:
                    result = await handler(record["context"])
                    await queue.complete(task_id, result)
                except Exception as exc:
                    logger.exception(
                        "Worker: unhandled exception executing %s/%s",
                        agent_id,
                        command,
                    )
                    await queue.fail(task_id, f"{type(exc).__name__}: {exc}")

    try:
        while True:
            record = await queue.claim(pool)
            if record is None:
                await asyncio.sleep(_POLL_INTERVAL)
                continue
            task = asyncio.create_task(_execute(record))
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)
    except asyncio.CancelledError:
        # Graceful shutdown: wait for in-flight tasks to complete
        if in_flight:
            logger.info(
                "Worker shutting down, waiting for %d in-flight tasks",
                len(in_flight),
            )
            _done, pending = await asyncio.wait(in_flight, timeout=_SHUTDOWN_TIMEOUT)
            if pending:
                logger.warning(
                    "Worker shutdown timeout, cancelling %d tasks",
                    len(pending),
                )
                for t in pending:
                    t.cancel()
        raise  # Re-raise CancelledError so caller sees it
