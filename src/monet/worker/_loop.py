"""Worker claim loop — pool-config-driven execution routing.

Pool backend drives the execution path:
  in_process   -> execute_task() in-worker registry
  cloudrun/ecs -> execute_cloud_push_workload() (fire-and-forget + poll)
  subprocess/docker/kubernetes + task       -> execute_managed_workload()
  subprocess/docker/kubernetes + persistent -> execute_persistent_workload()

Multi-pool: run_worker accepts ``pools`` (list) and round-robins over each
pool's claim endpoint, routing each claimed task to the correct workload
function based on the pool's backend and workload type.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

from monet.worker.gateway._auth import DEV_SIGNING_KEY, mint_task_token
from monet.worker.workload._collect import TaskFailure

if TYPE_CHECKING:
    from monet.config._pools import PoolConfig
    from monet.core.agent_loader import AgentEntryConfig
    from monet.core.registry import LocalRegistry
    from monet.events import TaskRecord
    from monet.progress._protocol import ProgressWriter
    from monet.queue import TaskQueue
    from monet.worker.execution._protocol import ExecutionBackend
    from monet.worker.transport._protocol import TransportAdapter
    from monet.worker.workload._router import TaskRouter

logger = logging.getLogger("monet.worker")

_PROGRESS_QUEUE_SIZE = 64
_DRAIN_PUBLISH_TIMEOUT = 1.0
_DEFAULT_GATEWAY_URL = "http://localhost:2027"
_DEFAULT_TOKEN_PADDING_S = 300.0


def _resolve_backend(pool: PoolConfig) -> ExecutionBackend:
    """Instantiate the correct ExecutionBackend for *pool*."""
    match pool.backend:
        case "subprocess":
            from monet.worker.execution._subprocess import SubprocessBackend

            return SubprocessBackend()
        case "docker":
            from monet.worker.execution._docker import DockerBackend

            return DockerBackend()
        case "cloudrun":
            from monet.worker.execution._cloudrun import CloudRunBackend

            return CloudRunBackend(
                project=pool.project or "",
                region=pool.region or "",
                job=pool.job or "",
                poll_interval_s=pool.poll_interval_s,
            )
        case "ecs":
            from monet.worker.execution._ecs import ECSBackend

            return ECSBackend(
                cluster=pool.cluster or "",
                task_definition=pool.task_definition or "",
                subnet_ids=list(pool.subnet_ids),
                security_groups=list(pool.security_groups),
            )
        case _:
            raise ValueError(
                f"No backend implementation for pool.backend={pool.backend!r}"
            )


def _resolve_transport(agent_cfg: AgentEntryConfig | None) -> TransportAdapter:
    """Instantiate the correct TransportAdapter for *agent_cfg*.

    Defaults to HTTPTransport when no agent config is available.
    """
    transport_type = agent_cfg.transport.type if agent_cfg is not None else "http"
    match transport_type:
        case "http":
            from monet.worker.transport._http import HTTPTransport

            return HTTPTransport()
        case "sse":
            from monet.worker.transport._sse import SSETransport

            return SSETransport()
        case "cli":
            from monet.worker.transport._cli import CLITransport

            return CLITransport()
        case _:
            raise ValueError(f"Unknown transport type: {transport_type!r}")


def _mint_token(record: TaskRecord, pool: PoolConfig, signing_key: str) -> str:
    ctx: dict[str, Any] = record.get("context") or {}  # type: ignore[assignment]
    return mint_task_token(
        task_id=record["task_id"],
        run_id=str(ctx.get("run_id", "")),
        pool=pool.name,
        scopes=["artifacts:read", "artifacts:write", "progress:write", "signals:write"],
        signing_key=signing_key,
        ttl_s=pool.task_timeout_s + _DEFAULT_TOKEN_PADDING_S,
    )


async def run_worker(
    queue: TaskQueue,
    registry: LocalRegistry | None = None,
    pool: str = "local",
    pools: list[str] | None = None,
    pool_configs: dict[str, PoolConfig] | None = None,
    agent_configs: dict[str, AgentEntryConfig] | None = None,
    gateway_url: str = "",
    signing_key: str = "",
    max_concurrency: int = 10,
    poll_interval: float = 0.1,
    shutdown_timeout: float = 30.0,
    task_timeout: float = 300.0,
    consumer_id: str | None = None,
    writer: ProgressWriter | None = None,
) -> None:
    """Pool-config-driven claim loop.

    Runs until the current ``asyncio.Task`` is cancelled. On cancellation,
    waits up to *shutdown_timeout* seconds for in-flight tasks to complete.

    Args:
        queue: Task queue to poll.
        registry: Handler registry for in-process execution. Defaults to the
            global registry populated by ``@agent`` decorators.
        pool: Single pool name (backwards-compat). Overridden by *pools*.
        pools: Ordered list of pool names this worker serves. If omitted,
            defaults to ``[pool]``.
        pool_configs: Pool configuration keyed by name. If omitted, defaults to
            a single ``in_process`` pool named ``"local"``.
        agent_configs: Agent configuration keyed by agent_id. Used to resolve
            transport type for external-backend pools. Optional — defaults to
            HTTP transport when a config entry is absent.
        gateway_url: Data plane gateway URL injected into agent env vars.
            Defaults to ``http://localhost:2027`` (embedded dev gateway).
        signing_key: JWT signing key for task-scoped tokens. Defaults to the
            dev signing key (``DEV_SIGNING_KEY``). Override in production.
        max_concurrency: Maximum simultaneous task executions across all pools.
        poll_interval: Seconds to sleep between claim rounds when all pools are
            empty.
        shutdown_timeout: Max seconds to wait for in-flight tasks on graceful
            shutdown.
        task_timeout: Max seconds an in-process agent handler may run before
            being failed with a timeout error. Not applied to external backends
            (those use pool.task_timeout_s).
        consumer_id: Stable worker identity for lease tracking. Auto-generated
            if omitted.
        writer: Optional ProgressWriter for direct progress persistence.
    """
    from monet.config._pools import PoolConfig as _PoolConfig

    if registry is None:
        from monet.core.registry import default_registry

        registry = default_registry

    import monet.hooks  # noqa: F401

    pool_names: list[str] = pools if pools is not None else [pool]
    _agent_configs: dict[str, AgentEntryConfig] = agent_configs or {}
    _signing_key = signing_key or DEV_SIGNING_KEY
    _gateway_url = gateway_url or _DEFAULT_GATEWAY_URL

    if pool_configs is None:
        pool_configs = {"local": _PoolConfig(name="local", backend="in_process")}

    # Build TaskRouter for persistent pools.
    persistent_pool_names = [
        n
        for n in pool_names
        if (pool_configs.get(n) or _PoolConfig(name=n, backend="in_process")).workload
        == "persistent"
        and (pool_configs.get(n) or _PoolConfig(name=n, backend="in_process")).backend
        != "in_process"
    ]
    router: TaskRouter | None = None
    if persistent_pool_names:
        from monet.worker.workload._router import TaskRouter

        persistent_cfgs = {
            n: pool_configs[n] for n in persistent_pool_names if n in pool_configs
        }
        router = TaskRouter(persistent_cfgs)

    sem = asyncio.Semaphore(max_concurrency)
    in_flight: set[asyncio.Task[None]] = set()

    # ── in-process execution path ─────────────────────────────────────────────

    async def _drain_progress(
        progress_q: asyncio.Queue[dict[str, Any]],
        shutdown: asyncio.Event,
        task_id: str,
    ) -> None:
        while not shutdown.is_set():
            get_task: asyncio.Task[dict[str, Any]] = asyncio.create_task(
                progress_q.get()
            )
            shutdown_task = asyncio.create_task(shutdown.wait())
            done, pending = await asyncio.wait(
                {get_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            if get_task in done:
                try:
                    data = get_task.result()
                except Exception:
                    continue
                try:
                    await queue.publish_progress(task_id, data)
                except Exception:
                    logger.debug(
                        "Failed to publish progress for task %s", task_id, exc_info=True
                    )

        while not progress_q.empty():
            try:
                data = progress_q.get_nowait()
                await asyncio.wait_for(
                    queue.publish_progress(task_id, data),
                    timeout=_DRAIN_PUBLISH_TIMEOUT,
                )
            except Exception:
                logger.debug(
                    "Failed to flush progress on shutdown for task %s",
                    task_id,
                    exc_info=True,
                )

    async def _execute_in_process(record: TaskRecord) -> None:
        task_id = record["task_id"]
        agent_id = record["agent_id"]
        command = record["command"]
        run_id = record["context"].get("run_id", "")
        thread_id = record["context"].get("thread_id", "")
        parent_call_id = record["context"].get("parent_call_id", "")

        async with sem:
            progress_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
                maxsize=_PROGRESS_QUEUE_SIZE
            )
            shutdown = asyncio.Event()
            drain_task = asyncio.create_task(
                _drain_progress(progress_q, shutdown, task_id)
            )

            from monet.queue._interface import QueueMaintenance

            heartbeat_task: asyncio.Task[None] | None = None
            if isinstance(queue, QueueMaintenance):
                heartbeat_interval = max(queue.lease_ttl_seconds / 3, 5.0)

                async def _heartbeat(tid: str = task_id) -> None:
                    while True:
                        await asyncio.sleep(heartbeat_interval)
                        try:
                            await queue.renew_lease(tid)
                        except Exception:
                            logger.debug("renew_lease failed for task %s", tid)

                heartbeat_task = asyncio.create_task(_heartbeat())

            def _publisher(data: dict[str, Any]) -> None:
                enriched = {
                    **data,
                    "run_id": str(run_id or ""),
                    "thread_id": str(thread_id or ""),
                    "parent_call_id": str(parent_call_id or ""),
                    "agent": agent_id,
                    "command": command,
                    "task_id": task_id,
                }
                try:
                    progress_q.put_nowait(enriched)
                except asyncio.QueueFull:
                    logger.debug("Progress queue full for task %s, dropping", task_id)

            async def _flush_drain() -> None:
                shutdown.set()
                with contextlib.suppress(asyncio.CancelledError):
                    await drain_task

            from monet.core.engine import execute_task

            try:
                await execute_task(
                    record,
                    registry,
                    queue,
                    publisher=_publisher,
                    writer=writer,
                    pool=record.get("pool", "local"),
                    task_timeout=task_timeout,
                    on_before_complete=_flush_drain,
                )
            finally:
                if not drain_task.done():
                    await _flush_drain()
                if heartbeat_task is not None and not heartbeat_task.done():
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task

    # ── external backend execution paths ─────────────────────────────────────

    async def _execute_external(record: TaskRecord, pool_cfg: PoolConfig) -> None:
        task_id = record["task_id"]
        async with sem:
            try:
                match pool_cfg.backend:
                    case "cloudrun" | "ecs":
                        from monet.worker.workload._persistent import (
                            execute_cloud_push_workload,
                        )

                        backend = _resolve_backend(pool_cfg)
                        token = _mint_token(record, pool_cfg, _signing_key)
                        gw_url = pool_cfg.gateway or _gateway_url
                        result = await execute_cloud_push_workload(
                            record, pool_cfg, backend, queue, gw_url, token
                        )
                        await queue.complete(task_id, result)

                    case _:  # subprocess, docker, kubernetes
                        agent_cfg = _agent_configs.get(record["agent_id"])
                        transport = _resolve_transport(agent_cfg)
                        gw_url = pool_cfg.gateway or _gateway_url
                        token = _mint_token(record, pool_cfg, _signing_key)
                        gateway_env = {
                            "MONET_GATEWAY_URL": gw_url,
                            "MONET_TOKEN": token,
                        }
                        if pool_cfg.workload == "persistent":
                            if router is None:
                                raise TaskFailure("no TaskRouter for persistent pool")
                            from monet.worker.workload._persistent import (
                                execute_persistent_workload,
                            )

                            result = await execute_persistent_workload(
                                record, pool_cfg.name, router, transport, queue
                            )
                        else:
                            if agent_cfg is None:
                                raise TaskFailure(
                                    f"no agent config for {record['agent_id']!r} "
                                    f"(required for managed workload)"
                                )
                            from monet.worker.workload._managed import (
                                execute_managed_workload,
                            )

                            backend = _resolve_backend(pool_cfg)
                            result = await execute_managed_workload(
                                record,
                                agent_cfg,
                                pool_cfg,
                                backend,
                                transport,
                                queue,
                                gateway_env,
                            )
                        await queue.complete(task_id, result)

            except TaskFailure as exc:
                logger.warning("task %s failed: %s", task_id, exc)
                await queue.fail(task_id, str(exc))
            except Exception:
                logger.exception("task %s failed with internal error", task_id)
                await queue.fail(task_id, "internal worker error")

    # ── dispatch ──────────────────────────────────────────────────────────────

    def _default_pool_cfg(name: str) -> PoolConfig:
        from monet.config._pools import PoolConfig as PoolConfigCls

        return PoolConfigCls(name=name, backend="in_process")

    async def _dispatch(record: TaskRecord) -> None:
        pool_name = record.get("pool") or pool_names[0]
        pool_cfg = pool_configs.get(pool_name) or _default_pool_cfg(pool_name)
        if pool_cfg.backend == "in_process":
            await _execute_in_process(record)
        else:
            await _execute_external(record, pool_cfg)

    # ── claim loop ────────────────────────────────────────────────────────────

    if consumer_id is None:
        consumer_id = f"worker-{uuid.uuid4().hex[:8]}"

    try:
        while True:
            claimed_any = False
            for pool_name in pool_names:
                pool_cfg = pool_configs.get(pool_name) or _default_pool_cfg(pool_name)

                # Back-pressure: skip persistent pools with no idle capacity.
                if (
                    router is not None
                    and pool_cfg.workload == "persistent"
                    and pool_cfg.backend != "in_process"
                    and not router.has_capacity(pool_name)
                ):
                    continue

                record = await queue.claim(
                    pool_name, consumer_id=consumer_id, block_ms=1
                )
                if record is None:
                    continue

                claimed_any = True
                task = asyncio.create_task(_dispatch(record))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)

            if not claimed_any:
                await asyncio.sleep(poll_interval)

    except asyncio.CancelledError:
        if in_flight:
            logger.info(
                "Worker shutting down, waiting for %d in-flight tasks", len(in_flight)
            )
            _done, pending = await asyncio.wait(in_flight, timeout=shutdown_timeout)
            if pending:
                logger.warning(
                    "Worker shutdown timeout, cancelling %d tasks", len(pending)
                )
                for t in pending:
                    t.cancel()
        raise
