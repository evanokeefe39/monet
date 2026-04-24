"""Queue worker — claims and executes tasks concurrently.

Follows the Prefect model: one worker per pool, claims any task in that
pool, looks up handler in local registry. Spawns concurrent asyncio.Tasks
with a configurable concurrency limit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from monet.core.registry import LocalRegistry
    from monet.queue import TaskQueue, TaskRecord

logger = logging.getLogger("monet.worker")

# Bounded internal queue that decouples sync emit_progress() calls in the
# agent from async publish_progress() transport.
_PROGRESS_QUEUE_SIZE = 64
# Per-publish timeout during drain-on-cancellation, so shutdown does not
# hang on a flaky backend.
_DRAIN_PUBLISH_TIMEOUT = 1.0
_tracer = trace.get_tracer("monet.worker")


async def run_worker(
    queue: TaskQueue,
    registry: LocalRegistry | None = None,
    pool: str = "local",
    max_concurrency: int = 10,
    poll_interval: float = 0.1,
    shutdown_timeout: float = 30.0,
    task_timeout: float = 300.0,
    consumer_id: str | None = None,
) -> None:
    """Poll queue by pool, execute concurrently via registry.

    Runs until the current ``asyncio.Task`` is cancelled. On cancellation,
    waits up to *shutdown_timeout* seconds for in-flight tasks to complete.

    Args:
        queue: The task queue to poll.
        registry: Handler registry for local execution. Defaults to the
            global registry populated by ``@agent`` decorators.
        pool: Pool name this worker serves. Claims only tasks in this pool.
        max_concurrency: Max concurrent task executions. Default 10.
        poll_interval: Seconds to sleep between claim attempts when the
            pool queue is empty. Default 0.1.
        shutdown_timeout: Max seconds to wait for in-flight tasks during
            graceful shutdown. Default 30.
        task_timeout: Max seconds a single task handler may run before
            being failed with a timeout error. Default 300.
    """
    if registry is None:
        from monet.core.registry import default_registry

        registry = default_registry

    # Import built-in worker hooks. @on_hook registrations fire at import
    # time into default_hook_registry. Idempotent — re-import is a no-op.
    import monet.hooks  # noqa: F401

    sem = asyncio.Semaphore(max_concurrency)
    in_flight: set[asyncio.Task[None]] = set()

    async def _drain_progress(
        progress_q: asyncio.Queue[dict[str, Any]],
        shutdown: asyncio.Event,
        task_id: str,
    ) -> None:
        """Forward events from the bounded in-process queue to the backend.

        Loops until ``shutdown`` is set. On shutdown, flushes any
        remaining queued events with a per-call timeout so worker
        teardown is bounded.
        """
        while not shutdown.is_set():
            # Race the next get() against shutdown.
            get_task: asyncio.Task[dict[str, Any]] = asyncio.create_task(
                progress_q.get()
            )
            shutdown_task = asyncio.create_task(shutdown.wait())
            done, pending = await asyncio.wait(
                {get_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            if get_task in done:
                try:
                    data = get_task.result()
                except Exception:
                    continue
                try:
                    await queue.publish_progress(task_id, data)
                except Exception:
                    logger.debug(
                        "Failed to publish progress for task %s",
                        task_id,
                        exc_info=True,
                    )

        # Shutdown: flush anything left in the queue.
        while not progress_q.empty():
            try:
                data = progress_q.get_nowait()
                await asyncio.wait_for(
                    queue.publish_progress(task_id, data),
                    timeout=_DRAIN_PUBLISH_TIMEOUT,
                )
            except Exception:
                logger.debug(
                    "Failed to flush progress on shutdown for task %s",
                    task_id,
                    exc_info=True,
                )

    async def _execute(record: TaskRecord) -> None:
        from monet.core.stubs import _progress_publisher

        task_id = record["task_id"]
        agent_id = record["agent_id"]
        command = record["command"]
        run_id = record["context"].get("run_id", "")
        thread_id = record["context"].get("thread_id", "")
        parent_call_id = record["context"].get("parent_call_id", "")

        async with sem:
            progress_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
                maxsize=_PROGRESS_QUEUE_SIZE
            )
            shutdown = asyncio.Event()
            drain_task = asyncio.create_task(
                _drain_progress(progress_q, shutdown, task_id)
            )

            def _publisher(data: dict[str, Any]) -> None:
                # Ensure thread_id and run_id are serializable strings
                enriched = {
                    **data,
                    "run_id": str(run_id or ""),
                    "thread_id": str(thread_id or ""),
                    "parent_call_id": str(parent_call_id or ""),
                    "agent": agent_id,
                    "command": command,
                    "task_id": task_id,
                }
                try:
                    progress_q.put_nowait(enriched)
                except asyncio.QueueFull:
                    logger.debug("Progress queue full for task %s, dropping", task_id)

            async def _flush_drain() -> None:
                """Signal drain to flush remaining events and wait for it."""
                shutdown.set()
                with contextlib.suppress(asyncio.CancelledError):
                    await drain_task

            token = _progress_publisher.set(_publisher)
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
                        # Flush progress BEFORE completing so subscribers
                        # see all events — completing the task triggers
                        # wait_completion cleanup which terminates subscriptions.
                        await _flush_drain()
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
                        await _flush_drain()
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
                        await _flush_drain()
                        await queue.fail(task_id, f"{type(exc).__name__}: {exc}")
            finally:
                _progress_publisher.reset(token)
                if not drain_task.done():
                    await _flush_drain()

    if consumer_id is None:
        consumer_id = f"worker-{uuid.uuid4().hex[:8]}"
    claim_block_ms = max(int(poll_interval * 1000), 1)

    try:
        while True:
            record = await queue.claim(
                pool, consumer_id=consumer_id, block_ms=claim_block_ms
            )
            if record is None:
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
            _done, pending = await asyncio.wait(in_flight, timeout=shutdown_timeout)
            if pending:
                logger.warning(
                    "Worker shutdown timeout, cancelling %d tasks",
                    len(pending),
                )
                for t in pending:
                    t.cancel()
        raise  # Re-raise CancelledError so caller sees it
