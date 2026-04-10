"""Server bootstrap — one-call init with guaranteed ordering.

Handles: tracing -> catalogue -> queue -> worker. Called once from the
server entry point (e.g., ``server_graphs.py``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from monet.core.manifest import AgentCapability

if TYPE_CHECKING:
    from monet.queue import TaskQueue


async def bootstrap(
    *,
    catalogue_root: str | Path | None = None,
    enable_tracing: bool = True,
    agents: list[AgentCapability] | None = None,
    queue: TaskQueue | None = None,
) -> asyncio.Task[None]:
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

    Returns:
        The background worker task. Cancel it on shutdown.
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
    from monet.core.queue_worker import run_worker
    from monet.core.registry import default_registry

    worker_task: asyncio.Task[Any] = asyncio.create_task(
        run_worker(queue, default_registry)
    )

    # Health monitoring: log if worker exits unexpectedly
    def _on_worker_done(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            import logging

            logging.getLogger("monet.server").error(
                "Worker task exited with exception: %s", exc
            )

    worker_task.add_done_callback(_on_worker_done)
    return worker_task


__all__ = ["AgentCapability", "bootstrap"]
