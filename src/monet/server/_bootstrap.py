"""Server bootstrap — one-call init with guaranteed ordering.

Handles: tracing -> artifacts -> queue -> worker. Called once from the
server entry point (e.g., ``server_graphs.py``).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from monet.config import ArtifactsConfig, ServerConfig
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
    from monet.core.registry import default_registry
    from monet.queue._worker import run_worker

    _worker_task: asyncio.Task[Any] | None = None
    _orig_enqueue = queue.enqueue

    async def _lazy_enqueue(task: Any) -> str:
        nonlocal _worker_task
        if _worker_task is None or _worker_task.done():
            _worker_task = asyncio.create_task(run_worker(queue, default_registry))
            _worker_task.add_done_callback(_on_worker_done)
        return await _orig_enqueue(task)

    def _on_worker_done(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            _log.error("Lazy worker exited with exception: %s", exc)

    queue.enqueue = _lazy_enqueue  # type: ignore[assignment]


async def bootstrap(
    *,
    artifacts_root: str | Path | None = None,
    enable_tracing: bool = True,
    agents: list[AgentCapability] | None = None,
    queue: TaskQueue | None = None,
    lazy_worker: bool = False,
) -> asyncio.Task[None] | None:
    """Initialize the monet server with guaranteed ordering.

    1. Configure tracing (if enabled)
    2. Configure artifact store (from artifacts_root or MONET_ARTIFACTS_DIR)
    3. Declare agent capabilities in manifest (if provided)
    4. Configure task queue (creates in-memory queue if none provided)
    5. Start in-process worker as a background task

    Args:
        artifacts_root: Path to artifact store directory. Falls back to
            MONET_ARTIFACTS_DIR env var, then to ``Path(".artifacts")``.
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
    # 0. Validate full server config and log a redacted summary before
    #    anything else fires. A typo or missing required value fails
    #    here rather than 500-ing on a later request.
    server_cfg = ServerConfig.load()
    server_cfg.validate_for_boot()
    _log.info("monet server booted: %s", server_cfg.redacted_summary())

    # 1. Tracing
    if enable_tracing:
        from monet.core.tracing import configure_tracing

        configure_tracing(server_cfg.observability)

    # 2. Artifact store
    # In monolith mode (default), the server configures the store and
    # the in-process worker inherits it. In distributed mode (set
    # MONET_DISTRIBUTED=1), the server has no need to read or write
    # artifacts — workers configure their own store at startup.
    artifacts_cfg = ArtifactsConfig.load()
    if not artifacts_cfg.distributed:
        from monet.artifacts import artifacts_from_env, configure_artifacts

        root = Path(artifacts_root) if artifacts_root else None
        service = artifacts_from_env(default_root=root)
        configure_artifacts(service)

    # 3. Manifest declarations (supplemental to @agent auto-declarations)
    from monet.agent_manifest import configure_agent_manifest
    from monet.core.manifest import default_manifest

    if agents:
        for cap in agents:
            default_manifest.declare(
                cap["agent_id"],
                cap["command"],
                description=cap.get("description", ""),
                pool=cap.get("pool", "local"),
            )

    # Monolith: configure manifest handle for in-process worker. Distributed
    # workers configure_agent_manifest() independently in their startup.
    configure_agent_manifest(default_manifest)

    # 4. Queue
    if queue is None:
        from monet.queue.backends.memory import InMemoryTaskQueue

        queue = InMemoryTaskQueue()

    from monet.orchestration._invoke import configure_queue

    configure_queue(queue)

    # 5. Worker
    if lazy_worker:
        configure_lazy_worker(queue)
        return None

    from monet.core.registry import default_registry
    from monet.queue._worker import run_worker

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
