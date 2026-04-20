"""Server utilities — bootstrap, configuration, and orchestration server."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI

    from monet.queue import TaskQueue

__all__ = ["create_app"]


def create_app(
    config_path: Path | None = None,
    queue: TaskQueue | None = None,
) -> FastAPI:
    """Create the monet orchestration server FastAPI application.

    Args:
        config_path: Path to monet.toml. When ``None``, resolves via
            :func:`monet.config.default_config_path` (which consults
            ``MONET_CONFIG_PATH`` and then ``Path.cwd() / "monet.toml"``).
        queue: Task queue implementation. Defaults to InMemoryTaskQueue.
    """
    import logging

    from fastapi import FastAPI as _FastAPI

    from monet.config import default_config_path
    from monet.orchestration import configure_capability_index
    from monet.server._capabilities import CapabilityIndex
    from monet.server._config import load_config
    from monet.server._deployment import DeploymentStore
    from monet.server._routes import router

    logger = logging.getLogger("monet.server")

    resolved_path = config_path if config_path is not None else default_config_path()
    config = load_config(resolved_path if resolved_path.exists() else None)

    if queue is None:
        from monet.queue.backends.memory import InMemoryTaskQueue

        queue = InMemoryTaskQueue()

    deployments = DeploymentStore(db_path=":memory:")
    capability_index = CapabilityIndex()
    configure_capability_index(capability_index)

    @asynccontextmanager
    async def lifespan(app: _FastAPI) -> AsyncIterator[None]:
        import time as _time

        from monet.queue import QueueMaintenance

        app.state.start_time = _time.monotonic()
        await deployments.initialize()

        # Periodic stale-deployment sweeper
        sweeper_task: asyncio.Task[None] | None = None
        queue_sweeper_task: asyncio.Task[None] | None = None

        async def _sweep_loop() -> None:
            while True:
                await asyncio.sleep(60)
                stale_worker_ids = (
                    await deployments.deactivate_stale_returning_worker_ids()
                )
                for wid in stale_worker_ids:
                    pruned = capability_index.drop_worker(wid)
                    if pruned:
                        logger.info(
                            "Stale worker %s pruned %d capability entries",
                            wid,
                            len(pruned),
                        )

        sweeper_task = asyncio.create_task(_sweep_loop())

        # Queue sweeper — reclaims entries whose lease has expired.
        # Only active when the backend supports maintenance operations.
        if isinstance(queue, QueueMaintenance):
            interval = max(queue.lease_ttl_seconds / 3, 5.0)

            async def _queue_sweep_loop() -> None:
                while True:
                    await asyncio.sleep(interval)
                    try:
                        await queue.reclaim_expired()
                    except Exception:
                        logger.exception("Queue sweeper failed")

            queue_sweeper_task = asyncio.create_task(_queue_sweep_loop())

        try:
            yield
        finally:
            if sweeper_task is not None:
                sweeper_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sweeper_task
            if queue_sweeper_task is not None:
                queue_sweeper_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await queue_sweeper_task
            await queue.close()
            from monet.orchestration import close_dispatch_client

            with contextlib.suppress(Exception):
                await close_dispatch_client()
            await deployments.close()

    app = _FastAPI(lifespan=lifespan)
    app.state.queue = queue
    app.state.deployments = deployments
    app.state.capability_index = capability_index
    app.state.config = config
    app.include_router(router)

    return app
