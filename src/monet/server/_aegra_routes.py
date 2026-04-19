"""Monet worker/task routes for Aegra custom HTTP mounting.

When running under Aegra (``aegra dev`` or ``aegra serve``), this module
provides monet's distribution control-plane routes (worker registration,
heartbeats, task claiming) as custom HTTP routes.  The task queue is
created once by :func:`~monet.server.server_bootstrap.bootstrap_server`
and shared with the graph execution layer via the canonical
``monet.orchestration._invoke._task_queue`` global.

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
from monet.queue.backends.redis_streams import RedisStreamsTaskQueue
from monet.server._capabilities import CapabilityIndex
from monet.server._deployment import DeploymentStore
from monet.server._routes import router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger("monet.server")


async def _reissue_in_flight_push(queue: object) -> None:
    """Re-dispatch push tasks that were in-flight when the server last crashed.

    Called once during lifespan startup, before the reclaim loop begins.
    Tasks that already exhausted all retries are failed immediately; others
    are re-dispatched via a fresh HTTP POST so Cloud Run / Lambda receives
    the dispatch envelope again.

    Hard-fails at boot when in-flight push records exist but MONET_API_KEY
    or MONET_SERVER_URL is unset — silent continuation would drop every
    recovering task with no indication of the root cause.
    """
    if not isinstance(queue, RedisStreamsTaskQueue):
        return
    records = await queue.list_in_flight_push_dispatches()
    if not records:
        return
    logger.info(
        "Reissuing %d in-flight push dispatch(es) from previous run",
        len(records),
    )

    from monet.config import MONET_SERVER_URL, AuthConfig
    from monet.config._env import read_str

    api_key = AuthConfig.load().api_key
    if not api_key:
        raise RuntimeError(
            f"Server has {len(records)} in-flight push-dispatch record(s) requiring "
            "re-dispatch but MONET_API_KEY is unset. "
            "Set MONET_API_KEY before restarting, or clear push-dispatch records."
        )
    api_url = read_str(MONET_SERVER_URL)
    if not api_url:
        raise RuntimeError(
            f"Server has {len(records)} in-flight push-dispatch record(s) requiring "
            "re-dispatch but MONET_SERVER_URL is unset."
        )

    from monet.orchestration._invoke import (
        _PUSH_MAX_ATTEMPTS,
        _push_with_retry,
        _write_dispatch_failed,
    )
    from monet.server._auth import task_hmac

    for rec in records:
        task_id = rec["task_id"]
        attempt = int(rec.get("attempt", 0))
        url = rec.get("url", "")
        dispatch_secret = rec.get("dispatch_secret") or None
        task_payload = rec.get("task_payload", "")

        if not url or not task_payload:
            logger.warning(
                "push_dispatch restart_recovery: incomplete record %s, skipping",
                task_id,
            )
            await queue.pop_push_dispatch(task_id)
            continue

        if attempt >= _PUSH_MAX_ATTEMPTS:
            logger.warning(
                "push_dispatch restart_recovery: task %s exhausted, failing",
                task_id,
            )
            await _write_dispatch_failed(
                task_id, queue, f"exhausted {attempt} attempts before server restart"
            )
            continue

        token = task_hmac(api_key, task_id)
        envelope = {
            "task_id": task_id,
            "token": token,
            "callback_url": f"{api_url.rstrip('/')}/api/v1/tasks/{task_id}",
            "payload": task_payload,
        }
        headers: dict[str, str] = {}
        if dispatch_secret:
            headers["Authorization"] = f"Bearer {dispatch_secret}"

        asyncio.create_task(  # noqa: RUF006
            _push_with_retry(
                task_id,
                queue,
                url,
                headers,
                envelope,
                task_payload,
                dispatch_secret=dispatch_secret,
            )
        )


_deployments = DeploymentStore()
_capability_index = CapabilityIndex()
configure_capability_index(_capability_index)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Server lifespan: queue boot + sweeper + in-process monolith worker.

    The in-process worker serves the S1 monolith scenario (``monet
    dev``): the server hosts the agent handlers and claims its own
    queue so a laptop can run the full pipeline without a separate
    ``monet worker`` process. The worker heartbeats in-process so the
    :class:`CapabilityIndex` reflects the same handlers the server can
    dispatch locally.

    ``bootstrap_server()`` is called here — not at module body — so the
    canonical lifespan is the single site that creates and wires the
    process-wide task queue.  Aegra loads graph modules via file-path
    re-imports (``aegra_graphs.*`` synthetic namespace); those re-runs
    of ``server_bootstrap.py`` skip queue creation because
    ``bootstrap_server()`` is idempotent and is never called from the
    file-path path.
    """
    import time as _time

    from monet.config import QueueConfig
    from monet.server.server_bootstrap import bootstrap_server

    _app.state.start_time = _time.monotonic()
    queue = bootstrap_server()
    _app.state.queue = queue

    await _deployments.initialize()
    await _reissue_in_flight_push(queue)

    cfg = QueueConfig.load()

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

    async def _reclaim_loop() -> None:
        interval = cfg.reclaim_interval_seconds
        while True:
            await asyncio.sleep(interval)
            if isinstance(queue, RedisStreamsTaskQueue):
                reclaimed = await queue.reclaim_expired_internal()
                if reclaimed:
                    logger.info(
                        "PEL sweeper reclaimed %d expired entries", len(reclaimed)
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
    reclaim_task = asyncio.create_task(_reclaim_loop())
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
        for task in (sweeper_task, reclaim_task, worker_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await _deployments.close()


app = FastAPI(lifespan=_lifespan)

# Deployments and capability index are available immediately.
# The queue is wired inside _lifespan via bootstrap_server() so it is
# never created at module-import time (which would re-run under Aegra's
# synthetic aegra_graphs.* namespace and split the queue singleton).
app.state.deployments = _deployments
app.state.capability_index = _capability_index

app.include_router(router)
