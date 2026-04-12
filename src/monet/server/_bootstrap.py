"""Server bootstrap — one-call init with guaranteed ordering.

Handles: tracing -> catalogue -> queue -> worker. Called once from the
server entry point (e.g., ``server_graphs.py``).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from monet.core.manifest import AgentCapability

if TYPE_CHECKING:
    from monet.queue import TaskQueue

_log = logging.getLogger("monet.server")


def configure_lazy_worker(queue: TaskQueue) -> None:
    """Patch *queue*.enqueue to start a background worker on first call.

    Use when the event loop is not available at configuration time —
    e.g., ``aegra dev`` creates its loop at graph invocation time,
    not at import time.  The worker is started via
    ``asyncio.create_task`` inside the first ``enqueue`` call, when the
    loop is guaranteed to be running.
    """
    from monet.core.queue_worker import run_worker
    from monet.core.registry import default_registry

    _worker_task: asyncio.Task[Any] | None = None
    _orig_enqueue = queue.enqueue

    async def _lazy_enqueue(
        agent_id: str, command: str, ctx: Any, pool: str = "local"
    ) -> str:
        nonlocal _worker_task
        if _worker_task is None or _worker_task.done():
            _worker_task = asyncio.create_task(run_worker(queue, default_registry))
            _worker_task.add_done_callback(_on_worker_done)
        return await _orig_enqueue(agent_id, command, ctx, pool=pool)

    def _on_worker_done(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            _log.error("Lazy worker exited with exception: %s", exc)

    queue.enqueue = _lazy_enqueue  # type: ignore[assignment]


async def bootstrap(
    *,
    catalogue_root: str | Path | None = None,
    enable_tracing: bool = True,
    agents: list[AgentCapability] | None = None,
    queue: TaskQueue | None = None,
    lazy_worker: bool = False,
) -> asyncio.Task[None] | None:
    """Initialize the monet server with guaranteed ordering.

    1. Configure tracing (if enabled)
    2. Configure catalogue (from catalogue_root or MONET_CATALOGUE_DIR)
    3. Declare agent capabilities in manifest (if provided)
    4. Configure task queue (creates in-memory queue if none provided)
    5. Start in-process worker as a background task

    Args:
        catalogue_root: Path to catalogue directory. Falls back to
            MONET_CATALOGUE_DIR env var, then to ``Path(".catalogue")``.
        enable_tracing: Whether to eagerly configure OpenTelemetry tracing.
        agents: Additional capabilities to declare in the manifest.
            Agents registered via ``@agent`` are auto-declared.
        queue: Task queue instance. Defaults to in-memory queue.
        lazy_worker: If True, defer worker startup to first enqueue.
            Use for ``aegra dev`` where the event loop is not
            available at import time.

    Returns:
        The background worker task, or None when *lazy_worker* is True.
        Cancel the task on shutdown when non-None.
    """
    # 1. Tracing
    if enable_tracing:
        from monet.core.tracing import configure_tracing

        configure_tracing()

    # 2. Catalogue
    from monet.catalogue import catalogue_from_env, configure_catalogue

    root = Path(catalogue_root) if catalogue_root else None
    service = catalogue_from_env(default_root=root)
    configure_catalogue(service)

    # 3. Manifest declarations (supplemental to @agent auto-declarations)
    if agents:
        from monet.core.manifest import default_manifest

        for cap in agents:
            default_manifest.declare(
                cap["agent_id"],
                cap["command"],
                description=cap.get("description", ""),
                pool=cap.get("pool", "local"),
            )

    # 4. Queue
    if queue is None:
        from monet.core.queue_memory import InMemoryTaskQueue

        queue = InMemoryTaskQueue()

    from monet.orchestration._invoke import configure_queue

    configure_queue(queue)

    # 5. Worker
    if lazy_worker:
        configure_lazy_worker(queue)
        return None

    from monet.core.queue_worker import run_worker
    from monet.core.registry import default_registry

    worker_task: asyncio.Task[Any] = asyncio.create_task(
        run_worker(queue, default_registry)
    )

    def _on_worker_done(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            _log.error("Worker task exited with exception: %s", exc)

    worker_task.add_done_callback(_on_worker_done)
    return worker_task


__all__ = ["AgentCapability", "bootstrap", "configure_lazy_worker"]
