"""monet worker — start a standalone worker process."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from monet.cli._discovery import DiscoveredAgent

logger = logging.getLogger("monet.cli.worker")


def _read_env_float(name: str, default: float) -> float:
    """Read a float from an environment variable with validation."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        msg = f"{name}={raw!r} is not a valid number"
        raise click.BadParameter(msg) from None
    if value <= 0:
        msg = f"{name}={raw!r} must be positive"
        raise click.BadParameter(msg)
    return value


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
    envvar="MONET_WORKER_POOL",
    help="Pool to claim tasks from.",
)
@click.option(
    "--concurrency",
    default=10,
    type=int,
    envvar="MONET_WORKER_CONCURRENCY",
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
@click.option(
    "--agents",
    "agents_file",
    envvar="MONET_WORKER_AGENTS",
    default=None,
    type=click.Path(exists=True),
    help="Path to agents.toml for declarative agent registration.",
)
def worker(
    path: str,
    pool: str,
    concurrency: int,
    server_url: str | None,
    api_key: str | None,
    agents_file: str | None,
) -> None:
    """Start a monet worker process.

    Scans --path for @agent decorated functions, imports them to
    populate the handler registry, and starts polling for tasks.

    Optionally loads --agents (agents.toml) for declarative registration
    of external agents (HTTP, SSE, CLI transports).

    In local mode (no --server-url): uses an in-memory queue.
    In remote mode (--server-url set): registers with the server,
    starts a heartbeat loop, and claims tasks via HTTP.

    Tuning env vars (rarely needed):
      MONET_WORKER_POLL_INTERVAL — seconds between claim attempts (default 0.1)
      MONET_WORKER_SHUTDOWN_TIMEOUT — graceful shutdown wait (default 30)
      MONET_WORKER_HEARTBEAT_INTERVAL — remote heartbeat cycle (default 30)
    """
    poll_interval = _read_env_float("MONET_WORKER_POLL_INTERVAL", 0.1)
    shutdown_timeout = _read_env_float("MONET_WORKER_SHUTDOWN_TIMEOUT", 30.0)
    heartbeat_interval = _read_env_float("MONET_WORKER_HEARTBEAT_INTERVAL", 30.0)
    asyncio.run(
        _run_worker(
            Path(path),
            pool,
            concurrency,
            server_url,
            api_key,
            agents_file=Path(agents_file) if agents_file else None,
            poll_interval=poll_interval,
            shutdown_timeout=shutdown_timeout,
            heartbeat_interval=heartbeat_interval,
        )
    )


async def _run_worker(
    path: Path,
    pool: str,
    concurrency: int,
    server_url: str | None,
    api_key: str | None,
    *,
    agents_file: Path | None = None,
    poll_interval: float = 0.1,
    shutdown_timeout: float = 30.0,
    heartbeat_interval: float = 30.0,
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

    # Load declarative agent config if provided.
    if agents_file is not None:
        from monet.core._agents_config import load_agents

        count = load_agents(agents_file)
        logger.info("Registered %d agent(s) from %s", count, agents_file)

    if server_url:
        await _run_remote(
            discovered,
            pool,
            concurrency,
            server_url,
            api_key,
            poll_interval=poll_interval,
            shutdown_timeout=shutdown_timeout,
            heartbeat_interval=heartbeat_interval,
        )
    else:
        await _run_local(
            pool,
            concurrency,
            poll_interval=poll_interval,
            shutdown_timeout=shutdown_timeout,
        )


async def _run_remote(
    discovered: list[DiscoveredAgent],
    pool: str,
    concurrency: int,
    server_url: str,
    api_key: str | None,
    *,
    poll_interval: float = 0.1,
    shutdown_timeout: float = 30.0,
    heartbeat_interval: float = 30.0,
) -> None:
    """Run worker in remote mode with HTTP-based queue."""
    import contextlib

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
            await asyncio.sleep(heartbeat_interval)
            # Read current capabilities each cycle so hot-reloads
            # are picked up on the next heartbeat. Transient failures
            # are handled inside heartbeat_with_tracking; 4xx auth
            # failures propagate and crash the loop (correct behavior
            # for a misconfigured API key).
            await client.heartbeat_with_tracking(
                worker_id, pool, _current_capabilities()
            )

    try:
        heartbeat_task = asyncio.create_task(_heartbeat_loop())
        queue = RemoteQueue(client, pool)
        await run_worker(
            queue,
            pool=pool,
            max_concurrency=concurrency,
            poll_interval=poll_interval,
            shutdown_timeout=shutdown_timeout,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Worker shutting down")
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        await client.close()


async def _run_local(
    pool: str,
    concurrency: int,
    *,
    poll_interval: float = 0.1,
    shutdown_timeout: float = 30.0,
) -> None:
    """Run worker in local mode with an in-memory queue."""
    from monet.orchestration._invoke import configure_queue
    from monet.queue import InMemoryTaskQueue, run_worker

    queue = InMemoryTaskQueue()
    configure_queue(queue)
    logger.info("Worker running in local mode (pool=%s)", pool)

    try:
        await run_worker(
            queue,
            pool=pool,
            max_concurrency=concurrency,
            poll_interval=poll_interval,
            shutdown_timeout=shutdown_timeout,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Worker shutting down")
