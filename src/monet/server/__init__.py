"""Server utilities — bootstrap, configuration, and orchestration server."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from monet.server._bootstrap import AgentCapability, bootstrap, configure_lazy_worker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI

    from monet.queue import TaskQueue

__all__ = ["AgentCapability", "bootstrap", "configure_lazy_worker", "create_app"]


def create_app(
    config_path: Path | None = None,
    queue: TaskQueue | None = None,
) -> FastAPI:
    """Create the monet orchestration server FastAPI application.

    Args:
        config_path: Path to monet.toml. Uses defaults if not provided.
        queue: Task queue implementation. Defaults to InMemoryTaskQueue.
    """
    import logging

    from fastapi import FastAPI as _FastAPI

    from monet.core.manifest import default_manifest
    from monet.server._config import load_config
    from monet.server._deployment import DeploymentStore
    from monet.server._routes import router

    logger = logging.getLogger("monet.server")

    config = load_config(config_path)

    if queue is None:
        from monet.core.queue_memory import InMemoryTaskQueue

        queue = InMemoryTaskQueue()

    deployments = DeploymentStore(db_path=":memory:")
    manifest = default_manifest

    @asynccontextmanager
    async def lifespan(app: _FastAPI) -> AsyncIterator[None]:
        await deployments.initialize()

        # Periodic stale-deployment sweeper
        sweeper_task: asyncio.Task[None] | None = None

        async def _sweep_loop() -> None:
            while True:
                await asyncio.sleep(60)
                stale_worker_ids = (
                    await deployments.deactivate_stale_returning_worker_ids()
                )
                for wid in stale_worker_ids:
                    removed = manifest.remove_by_worker(wid)
                    if removed:
                        logger.info(
                            "Removed %d capabilities for stale worker %s",
                            len(removed),
                            wid,
                        )

        sweeper_task = asyncio.create_task(_sweep_loop())

        try:
            yield
        finally:
            if sweeper_task is not None:
                sweeper_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sweeper_task
            await deployments.close()

    app = _FastAPI(lifespan=lifespan)
    app.state.queue = queue
    app.state.deployments = deployments
    app.state.manifest = manifest
    app.state.config = config
    app.include_router(router)

    return app
