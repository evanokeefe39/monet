"""monet worker — start a standalone worker process."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
import uuid
from pathlib import Path

import click

logger = logging.getLogger("monet.cli.worker")

# Heartbeat interval in seconds.
_HEARTBEAT_INTERVAL = 30.0


@click.command()
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True),
    help="Directory to scan for agents.",
)
@click.option(
    "--pool",
    default="local",
    help="Pool to claim tasks from.",
)
@click.option(
    "--concurrency",
    default=10,
    type=int,
    help="Max concurrent task executions.",
)
@click.option(
    "--server-url",
    envvar="MONET_SERVER_URL",
    default=None,
    help="Orchestration server URL.",
)
@click.option(
    "--api-key",
    envvar="MONET_API_KEY",
    default=None,
    help="API key for server auth.",
)
def worker(
    path: str,
    pool: str,
    concurrency: int,
    server_url: str | None,
    api_key: str | None,
) -> None:
    """Start a monet worker process.

    Scans --path for @agent decorated functions, imports them to
    populate the handler registry, and starts polling for tasks.

    In local mode (no --server-url): uses an in-memory queue.
    In remote mode (--server-url set): registers with the server,
    starts a heartbeat loop, and claims tasks via HTTP.
    """
    asyncio.run(_run_worker(Path(path), pool, concurrency, server_url, api_key))


async def _run_worker(
    path: Path,
    pool: str,
    concurrency: int,
    server_url: str | None,
    api_key: str | None,
) -> None:
    """Discover agents, configure queue, and run the worker loop."""
    from monet.cli._discovery import discover_agents

    discovered = discover_agents(path)
    logger.info("Discovered %d agent(s) in %s", len(discovered), path)

    # Deduplicate files to avoid double-importing.
    discovered_files: list[Path] = list(dict.fromkeys(a.file for a in discovered))

    # Import discovered files to populate the handler registry.
    for agent_file in discovered_files:
        spec = importlib.util.spec_from_file_location(agent_file.stem, agent_file)
        if spec is None or spec.loader is None:
            logger.warning("Could not load %s, skipping", agent_file)
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[agent_file.stem] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        logger.info("Imported %s", agent_file)

    if server_url:
        await _run_remote(discovered, pool, concurrency, server_url, api_key)
    else:
        await _run_local(pool, concurrency)


async def _run_remote(
    discovered: list,  # type: ignore[type-arg]
    pool: str,
    concurrency: int,
    server_url: str,
    api_key: str | None,
) -> None:
    """Run worker in remote mode with HTTP-based queue."""
    import contextlib

    import httpx

    from monet.core.manifest import AgentCapability, default_manifest
    from monet.core.worker_client import RemoteQueue, WorkerClient
    from monet.queue import run_worker

    def _current_capabilities() -> list[AgentCapability]:
        """Read capabilities from the manifest with descriptions."""
        return [
            AgentCapability(
                agent_id=cap["agent_id"],
                command=cap["command"],
                description=cap.get("description", ""),
                pool=cap.get("pool", pool),
            )
            for cap in default_manifest.capabilities()
        ]

    client = WorkerClient(server_url, api_key or "")
    worker_id = uuid.uuid4().hex[:8]

    capabilities = _current_capabilities()

    await client.register(pool, capabilities, worker_id)
    logger.info(
        "Registered worker %s with %d capabilities",
        worker_id,
        len(capabilities),
    )

    heartbeat_task: asyncio.Task[None] | None = None

    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            # Read current capabilities each cycle so hot-reloads
            # are picked up on the next heartbeat.
            try:
                await client.heartbeat(worker_id, pool, _current_capabilities())
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("Heartbeat failed: %s", exc)

    try:
        heartbeat_task = asyncio.create_task(_heartbeat_loop())
        queue = RemoteQueue(client, pool)
        await run_worker(queue, pool=pool, max_concurrency=concurrency)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Worker shutting down")
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        await client.close()


async def _run_local(pool: str, concurrency: int) -> None:
    """Run worker in local mode with an in-memory queue."""
    from monet.orchestration._invoke import configure_queue
    from monet.queue import InMemoryTaskQueue, run_worker

    queue = InMemoryTaskQueue()
    configure_queue(queue)
    logger.info("Worker running in local mode (pool=%s)", pool)

    try:
        await run_worker(queue, pool=pool, max_concurrency=concurrency)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Worker shutting down")
