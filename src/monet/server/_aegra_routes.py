"""Monet worker/task routes for Aegra custom HTTP mounting.

When running under Aegra (``aegra dev`` or ``aegra serve``), this module
provides monet's distribution control-plane routes (worker registration,
heartbeats, task claiming) as custom HTTP routes.  The task queue is
shared with the graph execution layer via ``server_bootstrap.queue``.

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

from monet.orchestration._invoke import configure_capability_index
from monet.server._capabilities import CapabilityIndex
from monet.server._deployment import DeploymentStore
from monet.server._routes import router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger("monet.server")

_deployments = DeploymentStore()
_capability_index = CapabilityIndex()
configure_capability_index(_capability_index)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Server lifespan: sweeper + in-process monolith worker.

    The in-process worker serves the S1 monolith scenario (``monet
    dev``): the server hosts the agent handlers and claims its own
    queue so a laptop can run the full pipeline without a separate
    ``monet worker`` process. The worker heartbeats in-process so the
    :class:`CapabilityIndex` reflects the same handlers the server can
    dispatch locally.
    """
    await _deployments.initialize()

    async def _sweep_loop() -> None:
        while True:
            await asyncio.sleep(60)
            stale_worker_ids = (
                await _deployments.deactivate_stale_returning_worker_ids()
            )
            for wid in stale_worker_ids:
                pruned = _capability_index.drop_worker(wid)
                if pruned:
                    logger.info(
                        "Stale worker %s pruned %d capability entries",
                        wid,
                        len(pruned),
                    )

    # Register reference agents via the explicit helper — a bare import
    # is a no-op after the first load (sys.modules), which means test
    # scopes that roll back ``default_registry`` leave the worker with
    # an empty handler set.
    from monet.agents import register_reference_agents
    from monet.core.registry import default_registry
    from monet.queue._worker import run_worker
    from monet.server._capabilities import Capability

    register_reference_agents()

    in_proc_worker_id = "monolith-0"
    capabilities = [
        Capability(
            agent_id=row.agent_id,
            command=row.command,
            pool="local",
            description=row.description,
        )
        for row in default_registry.registered_agents(with_docstrings=True)
    ]
    _capability_index.upsert_worker(in_proc_worker_id, "local", capabilities)

    sweeper_task = asyncio.create_task(_sweep_loop())
    worker_task = asyncio.create_task(
        run_worker(queue, pool="local", consumer_id=in_proc_worker_id)
    )
    logger.info(
        "monolith in-process worker started: worker_id=%s capabilities=%d",
        in_proc_worker_id,
        len(capabilities),
    )
    try:
        yield
    finally:
        for task in (sweeper_task, worker_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await _deployments.close()


app = FastAPI(lifespan=_lifespan)

# Wire dependencies — queue comes from server_bootstrap module-level init.
# Aegra imports server_bootstrap first (to load graphs), so the queue is
# already configured by the time this module loads.
from monet.server.server_bootstrap import queue  # noqa: E402

app.state.queue = queue
app.state.deployments = _deployments
app.state.capability_index = _capability_index

app.include_router(router)
