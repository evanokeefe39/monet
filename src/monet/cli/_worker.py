"""monet worker — start a standalone worker process."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import click

from monet.config import (
    GEMINI_API_KEY,
    GROQ_API_KEY,
    MONET_API_KEY,
    MONET_SERVER_URL,
    MONET_WORKER_AGENTS,
    MONET_WORKER_CONCURRENCY,
    MONET_WORKER_POOL,
    ConfigError,
    WorkerConfig,
)

if TYPE_CHECKING:
    from monet.cli._discovery import DiscoveredAgent

logger = logging.getLogger("monet.cli.worker")


@click.command()
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True),
    help="Directory to scan for agents.",
)
@click.option(
    "--pool",
    default=None,
    envvar=MONET_WORKER_POOL,
    help="Pool to claim tasks from (default: local).",
)
@click.option(
    "--concurrency",
    default=None,
    type=int,
    envvar=MONET_WORKER_CONCURRENCY,
    help="Max concurrent task executions (default: 10).",
)
@click.option(
    "--server-url",
    envvar=MONET_SERVER_URL,
    default=None,
    help="Orchestration server URL.",
)
@click.option(
    "--api-key",
    envvar=MONET_API_KEY,
    default=None,
    help="API key for server auth.",
)
@click.option(
    "--agents",
    "agents_file",
    envvar=MONET_WORKER_AGENTS,
    default=None,
    type=click.Path(exists=True),
    help="Path to agents.toml for declarative agent registration.",
)
def worker(
    path: str,
    pool: str | None,
    concurrency: int | None,
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
    overrides: dict[str, object] = {}
    if pool is not None:
        overrides["pool"] = pool
    if concurrency is not None:
        overrides["concurrency"] = concurrency
    if server_url is not None:
        overrides["server_url"] = server_url
    if api_key is not None:
        overrides["api_key"] = api_key
    if agents_file is not None:
        overrides["agents_toml"] = Path(agents_file)

    cfg = WorkerConfig.load().model_copy(update=overrides)
    # Reference agents need at least one LLM provider key. A worker that
    # boots without one will successfully claim tasks and then fail the
    # moment the first agent tries to instantiate a model — far from the
    # cause. Require at least one common LLM key at boot instead.
    cfg = cfg.with_required_llm_keys((GEMINI_API_KEY, GROQ_API_KEY))
    try:
        cfg.validate_for_boot()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from None

    logger.info("monet worker booted: %s", cfg.redacted_summary())
    asyncio.run(_run_worker(Path(path), cfg))


def _import_agents(path: Path, cfg: WorkerConfig) -> None:
    """Discover + import agents and load declarative agents.toml."""
    from monet.cli._discovery import discover_agents

    discovered = discover_agents(path)
    logger.info("Discovered %d agent(s) in %s", len(discovered), path)
    discovered_files: list[Path] = list(dict.fromkeys(a.file for a in discovered))
    for agent_file in discovered_files:
        spec = importlib.util.spec_from_file_location(agent_file.stem, agent_file)
        if spec is None or spec.loader is None:
            logger.warning("Could not load %s, skipping", agent_file)
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[agent_file.stem] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        logger.info("Imported %s", agent_file)
    if cfg.agents_toml is not None:
        from monet.core._agents_config import load_agents

        count = load_agents(cfg.agents_toml)
        logger.info("Registered %d agent(s) from %s", count, cfg.agents_toml)


async def _run_worker(path: Path, cfg: WorkerConfig) -> None:
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
    if cfg.agents_toml is not None:
        from monet.core._agents_config import load_agents

        count = load_agents(cfg.agents_toml)
        logger.info("Registered %d agent(s) from %s", count, cfg.agents_toml)

    if cfg.server_url:
        await _run_remote(discovered, cfg)
    else:
        await _run_local(cfg)


async def _run_remote(
    discovered: list[DiscoveredAgent],
    cfg: WorkerConfig,
) -> None:
    """Run worker in remote mode with HTTP-based queue."""
    import contextlib

    from monet.core.registry import default_registry
    from monet.server._capabilities import Capability
    from monet.worker import RemoteQueue, WorkerClient, run_worker

    pool = cfg.pool

    def _current_capabilities() -> list[Capability]:
        """Read capabilities from the local registry with docstring descriptions."""
        return [
            Capability(
                agent_id=row.agent_id,
                command=row.command,
                description=row.description,
                pool=pool,
            )
            for row in default_registry.registered_agents(with_docstrings=True)
        ]

    assert cfg.server_url is not None  # validated in validate_for_boot
    client = WorkerClient(cfg.server_url, cfg.api_key or "")
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
            await asyncio.sleep(cfg.heartbeat_interval)
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
            max_concurrency=cfg.concurrency,
            poll_interval=cfg.poll_interval,
            shutdown_timeout=cfg.shutdown_timeout,
            consumer_id=worker_id,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Worker shutting down")
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        await client.close()


async def _run_local(cfg: WorkerConfig) -> None:
    """Run worker in local mode with an in-memory queue."""
    from monet.orchestration._invoke import configure_queue
    from monet.queue import InMemoryTaskQueue
    from monet.worker import run_worker

    queue = InMemoryTaskQueue()
    configure_queue(queue)
    logger.info("Worker running in local mode (pool=%s)", cfg.pool)

    try:
        await run_worker(
            queue,
            pool=cfg.pool,
            max_concurrency=cfg.concurrency,
            poll_interval=cfg.poll_interval,
            shutdown_timeout=cfg.shutdown_timeout,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Worker shutting down")
