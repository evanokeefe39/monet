"""Monet worker/task routes for Aegra custom HTTP mounting.

When running under Aegra (``aegra dev`` or ``aegra serve``), this module
provides monet's distribution control-plane routes (worker registration,
heartbeats, task claiming) as custom HTTP routes.  The task queue is
shared with the graph execution layer via ``default_graphs.queue``.

Configure in ``aegra.json``::

    {
      "http": {
        "app": "monet.server._aegra_routes:app"
      }
    }
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from monet.core.manifest import default_manifest
from monet.server._deployment import DeploymentStore
from monet.server._routes import router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger("monet.server")

_deployments = DeploymentStore()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _deployments.initialize()

    async def _sweep_loop() -> None:
        while True:
            await asyncio.sleep(60)
            stale_worker_ids = (
                await _deployments.deactivate_stale_returning_worker_ids()
            )
            for wid in stale_worker_ids:
                removed = default_manifest.remove_by_worker(wid)
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
        sweeper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper_task
        await _deployments.close()


app = FastAPI(lifespan=_lifespan)

# Wire dependencies — queue comes from default_graphs module-level init.
# Aegra imports default_graphs first (to load graphs), so the queue is
# already configured by the time this module loads.
from monet.server.default_graphs import queue  # noqa: E402

app.state.queue = queue
app.state.deployments = _deployments
app.state.manifest = default_manifest

app.include_router(router)
