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

    from monet.artifacts._protocol import ArtifactClient
    from monet.queue import TaskQueue
    from monet.queue._progress import ProgressReader, ProgressWriter
    from monet.server._capabilities import CapabilityIndex

from monet.config._schema import ChatConfig

__all__ = [
    "create_app",
    "create_control_app",
    "create_data_app",
    "create_unified_app",
]


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

        # Model smoke test — fail fast if models are decomm'd or keys invalid
        chat_cfg = ChatConfig.load()
        if not chat_cfg.skip_smoke_test:
            from monet.server._smoke import smoke_test_models

            await smoke_test_models(chat_cfg)

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
            await deployments.close()

    app = _FastAPI(lifespan=lifespan)
    app.state.queue = queue
    app.state.deployments = deployments
    app.state.capability_index = capability_index
    app.state.config = config
    app.include_router(router)

    return app


def create_unified_app(
    queue: TaskQueue,
    capability_index: CapabilityIndex,
    writer: ProgressWriter | None = None,
    reader: ProgressReader | None = None,
    artifact_store: ArtifactClient | None = None,
) -> FastAPI:
    """Create a unified app with both control and data plane routes.

    Intended for S1/S2/S3 deployments where a single process serves all
    planes. The data-plane routes (typed events, SSE) are only active when
    *writer* and *reader* are provided; they return 501 otherwise.
    """
    from fastapi import FastAPI as _FastAPI

    from monet.server._deployment import DeploymentStore
    from monet.server.routes import router as _router

    app = _FastAPI()
    deployments = DeploymentStore(db_path=":memory:")
    app.state.queue = queue
    app.state.deployments = deployments
    app.state.capability_index = capability_index
    if writer is not None:
        app.state.progress_writer = writer
    if reader is not None:
        app.state.progress_reader = reader
    if artifact_store is not None:
        app.state.artifact_store = artifact_store
    app.include_router(_router)
    return app


def create_control_app(
    queue: TaskQueue,
    capability_index: CapabilityIndex,
) -> FastAPI:
    """Create a control-plane-only app.

    Mounts: worker heartbeat, task claim/complete/fail, thread inspection,
    invocations, health. Accepts no ProgressWriter or ArtifactStore — the
    data boundary is enforced by the type system.
    """
    from fastapi import APIRouter
    from fastapi import FastAPI as _FastAPI

    from monet.server._deployment import DeploymentStore
    from monet.server.routes import (
        _invocations,
        _ops,
        _tasks_control,
        _threads,
        _workers,
    )

    app = _FastAPI()
    deployments = DeploymentStore(db_path=":memory:")
    app.state.queue = queue
    app.state.deployments = deployments
    app.state.capability_index = capability_index

    control_router = APIRouter(prefix="/api/v1")
    control_router.include_router(_workers.router)
    control_router.include_router(_tasks_control.router)
    control_router.include_router(_threads.router)
    control_router.include_router(_ops.router)
    control_router.include_router(_invocations.router)
    app.include_router(control_router)
    return app


def create_data_app(
    writer: ProgressWriter,
    reader: ProgressReader,
    artifact_store: ArtifactClient | None = None,
) -> FastAPI:
    """Create a data-plane-only app.

    Mounts: typed event record/query, legacy progress endpoints, artifact
    CRUD, health. Accepts no TaskQueue or CapabilityIndex.
    """
    from fastapi import APIRouter
    from fastapi import FastAPI as _FastAPI

    from monet.server.routes import _artifacts, _ops, _tasks_data

    app = _FastAPI()
    app.state.progress_writer = writer
    app.state.progress_reader = reader
    if artifact_store is not None:
        app.state.artifact_store = artifact_store

    data_router = APIRouter(prefix="/api/v1")
    data_router.include_router(_tasks_data.router)
    data_router.include_router(_artifacts.router)
    data_router.include_router(_ops.router)
    app.include_router(data_router)
    return app
