"""ContainerSupervisor — lifecycle management for subprocess and docker backends.

Provides warm-pool startup, liveness checking, restart-with-backoff, circuit
breaking, graceful drain, and orphan reconciliation.

Not used by Kubernetes or cloud-push (cloudrun/ecs) backends — those delegate
supervision entirely to the cloud runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from monet.worker.execution._protocol import ContainerSpec, ExecutionBackend, JobStatus
from monet.worker.workload._router import ManagedInstance, TaskRouter

if TYPE_CHECKING:
    from monet.config._pools import PoolConfig

__all__ = ["ContainerSupervisor"]

_log = logging.getLogger("monet.worker.workload._supervisor")


class ContainerSupervisor:
    """Manages the lifecycle of subprocess and docker agent pools.

    Each pool is registered via ``start_pool``, which stores the backend,
    config, and env vars for subsequent ``check_liveness``, ``restart_instance``,
    and ``drain`` calls.

    Restart policy:
        Restarts are counted in a rolling window (``config.restart_window_s``).
        When the count reaches ``config.max_restarts``, the circuit opens and
        ``restart_instance`` returns None — the dead instance stays dead until
        the window rolls over or the worker is restarted.

    Orphan reconciliation:
        ``reconcile_orphans`` is a no-op by default because the ExecutionBackend
        protocol does not expose container enumeration.  A future
        ``ListContainersBackend`` protocol extension will enable docker-label-based
        reconciliation.
    """

    def __init__(self) -> None:
        self._backends: dict[str, ExecutionBackend] = {}
        self._configs: dict[str, PoolConfig] = {}
        self._envs: dict[str, dict[str, str]] = {}
        # Maps process_id -> list of monotonic restart timestamps.
        self._restart_log: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Pool startup
    # ------------------------------------------------------------------

    async def start_pool(
        self,
        pool: str,
        config: PoolConfig,
        backend: ExecutionBackend,
        env: dict[str, str],
    ) -> list[ManagedInstance]:
        """Start the warm pool for *pool* and return the instances.

        Registers the backend, config, and env for this pool so that
        subsequent supervisor calls can act on them without re-passing
        the same arguments.

        Args:
            pool: Pool name.
            config: Pool configuration (image, warm_pool_size, etc.).
            backend: Execution backend for this pool.
            env: Base env vars injected into every instance
                (typically includes MONET_GATEWAY_URL but not MONET_TOKEN).

        Returns:
            List of :class:`ManagedInstance` objects in ``"idle"`` state.
        """
        self._backends[pool] = backend
        self._configs[pool] = config
        self._envs[pool] = env

        instances: list[ManagedInstance] = []
        spec = ContainerSpec(
            image=config.image,
            expose_port=config.agent_port,
            labels={"monet.pool": pool},
        )
        for i in range(config.warm_pool_size):
            try:
                endpoint = await backend.start(spec, env)
                instances.append(
                    ManagedInstance(pool=pool, endpoint=endpoint, state="idle")
                )
                _log.debug(
                    "Started warm instance %d/%d for pool %s",
                    i + 1,
                    config.warm_pool_size,
                    pool,
                )
            except Exception:
                _log.exception(
                    "Failed to start warm instance %d for pool %s", i + 1, pool
                )
        return instances

    # ------------------------------------------------------------------
    # Liveness
    # ------------------------------------------------------------------

    async def check_liveness(self, instance: ManagedInstance) -> bool:
        """Check whether *instance* is still alive.

        Marks the instance as dead in-place when the backend reports it has
        exited. Returns True if the process is running, False otherwise.
        """
        backend = self._backends.get(instance.pool)
        if backend is None:
            return False
        status = await backend.poll_status(instance.endpoint)
        alive = status == JobStatus.RUNNING
        if not alive and instance.state != "dead":
            instance.state = "dead"
            _log.warning(
                "Instance %s in pool %s is no longer running (status=%s)",
                instance.endpoint.process_id,
                instance.pool,
                status.value,
            )
        return alive

    # ------------------------------------------------------------------
    # Restart with circuit breaker
    # ------------------------------------------------------------------

    async def restart_instance(
        self,
        pool: str,
        instance: ManagedInstance,
    ) -> ManagedInstance | None:
        """Restart a failed instance, respecting the circuit breaker.

        Counts restarts in ``config.restart_window_s``. When the count
        reaches ``config.max_restarts``, the circuit opens and this method
        returns None (instance should be removed from the pool).

        Args:
            pool: Pool name.
            instance: The dead instance to replace.

        Returns:
            A new :class:`ManagedInstance` in ``"idle"`` state, or None if
            the circuit is open.
        """
        config = self._configs.get(pool)
        backend = self._backends.get(pool)
        env = self._envs.get(pool)
        if config is None or backend is None or env is None:
            _log.error("restart_instance called for unregistered pool %s", pool)
            return None

        pid = instance.endpoint.process_id
        now = time.monotonic()
        window_start = now - config.restart_window_s

        # Prune timestamps outside the window.
        timestamps = [t for t in self._restart_log.get(pid, []) if t > window_start]

        if len(timestamps) >= config.max_restarts:
            _log.error(
                "Circuit open for instance %s in pool %s: %d restarts in %.0fs window",
                pid,
                pool,
                len(timestamps),
                config.restart_window_s,
            )
            return None

        timestamps.append(now)
        self._restart_log[pid] = timestamps

        # Best-effort stop before replacing.
        with contextlib.suppress(Exception):
            await backend.stop(instance.endpoint, 0.0)

        spec = ContainerSpec(
            image=config.image,
            expose_port=config.agent_port,
            labels={"monet.pool": pool},
        )
        try:
            new_endpoint = await backend.start(spec, env)
            _log.info("Restarted instance in pool %s (was %s)", pool, pid)
            return ManagedInstance(pool=pool, endpoint=new_endpoint, state="idle")
        except Exception:
            _log.exception("Failed to restart instance in pool %s", pool)
            return None

    # ------------------------------------------------------------------
    # Drain
    # ------------------------------------------------------------------

    async def drain(self, pool: str, router: TaskRouter) -> None:
        """Drain *pool* — block new acquires and wait for in-flight tasks.

        Sets the pool's draining flag on the router, then polls until no
        instances are in the ``"busy"`` state. Stops all live instances
        after the pool is idle.

        Args:
            pool: Pool name.
            router: The TaskRouter holding the instance state.
        """
        backend = self._backends.get(pool)
        config = self._configs.get(pool)
        router.set_draining(pool, draining=True)
        _log.info("Draining pool %s", pool)

        # Wait for all busy instances to finish.
        while True:
            instances = router.get_instances(pool)
            if not any(i.state == "busy" for i in instances):
                break
            await asyncio.sleep(0.5)

        # Stop all remaining live instances.
        if backend and config:
            instances = router.get_instances(pool)
            for inst in instances:
                if inst.state != "dead":
                    with contextlib.suppress(Exception):
                        await backend.stop(inst.endpoint, config.graceful_shutdown_s)
                    inst.state = "dead"

        _log.info("Pool %s drained", pool)

    # ------------------------------------------------------------------
    # Orphan reconciliation
    # ------------------------------------------------------------------

    async def reconcile_orphans(
        self,
        pool: str,
        worker_id: str,
    ) -> int:
        """Kill containers from a previous worker incarnation.

        Currently a no-op — the ExecutionBackend protocol does not expose
        container enumeration by label. Returns 0. A future extension will
        add an optional ``ListContainersBackend`` protocol so DockerBackend
        can implement this.

        Args:
            pool: Pool name.
            worker_id: Current worker identifier. Orphans belong to a
                different worker_id.

        Returns:
            Number of orphaned containers killed (always 0 until protocol
            extension is implemented).
        """
        _log.debug(
            "reconcile_orphans: pool=%s worker_id=%s"
            " — skipped (not supported by current backend)",
            pool,
            worker_id,
        )
        return 0
