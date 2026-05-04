"""TaskRouter — idle/busy tracking for persistent agent pools.

Backend-agnostic. Manages the set of ManagedInstance objects for each pool,
serialising acquire/release with an asyncio.Condition so acquire_idle blocks
when all instances are temporarily busy rather than returning None.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.config._pools import PoolConfig
    from monet.worker.execution._protocol import Endpoint

__all__ = ["ManagedInstance", "TaskRouter"]

_log = logging.getLogger("monet.worker.workload._router")


@dataclass
class ManagedInstance:
    """A single agent process managed by the router.

    Attributes:
        pool: Pool this instance belongs to.
        endpoint: How to reach the running agent process.
        state: Lifecycle state — ``"idle"`` (ready for work),
            ``"busy"`` (executing a task), or ``"dead"`` (process exited).
    """

    pool: str
    endpoint: Endpoint
    state: str = "idle"


class TaskRouter:
    """Idle/busy tracking for persistent agent pools.

    Each pool has a list of ManagedInstance objects.  Workers call
    ``acquire_idle`` before dispatching a task and ``release`` when done.
    The router uses a single asyncio.Condition so ``acquire_idle`` blocks
    (rather than returning None) when all live instances are busy.

    It returns None only when the pool is draining or has no live instances.

    Thread safety: all methods must be called from the same event loop.
    """

    def __init__(self, pool_configs: dict[str, PoolConfig]) -> None:
        self._pool_configs = pool_configs
        self._instances: dict[str, list[ManagedInstance]] = {
            name: [] for name in pool_configs
        }
        self._draining: dict[str, bool] = {name: False for name in pool_configs}
        # Single condition guards all pools — pools are small so contention is minimal.
        self._condition: asyncio.Condition = asyncio.Condition()

    # ------------------------------------------------------------------
    # Instance registration (called by ContainerSupervisor.start_pool)
    # ------------------------------------------------------------------

    def add_instance(self, pool: str, instance: ManagedInstance) -> None:
        """Register a new instance in the router.

        Must be called before the instance can be acquired. Notifies waiters
        in case acquire_idle is blocked waiting for an idle slot.
        """
        self._instances.setdefault(pool, []).append(instance)

    def remove_instance(self, pool: str, instance: ManagedInstance) -> None:
        """Deregister an instance (e.g. after it is confirmed dead)."""
        bucket = self._instances.get(pool, [])
        if instance in bucket:
            bucket.remove(instance)

    def get_instances(self, pool: str) -> list[ManagedInstance]:
        """Return the current instance list for *pool*."""
        return list(self._instances.get(pool, []))

    # ------------------------------------------------------------------
    # Acquire / release
    # ------------------------------------------------------------------

    async def acquire_idle(self, pool: str) -> ManagedInstance | None:
        """Acquire an idle instance from *pool*, blocking until one is available.

        Returns:
            An idle :class:`ManagedInstance` with ``state`` set to ``"busy"``,
            or ``None`` when the pool is draining or has no live instances.
        """
        async with self._condition:
            while True:
                if self._draining.get(pool):
                    return None
                instances = self._instances.get(pool, [])
                live = [i for i in instances if i.state != "dead"]
                if not live:
                    return None
                idle = [i for i in live if i.state == "idle"]
                if idle:
                    chosen = idle[0]
                    chosen.state = "busy"
                    return chosen
                # All live instances are busy — wait for a release.
                await self._condition.wait()

    async def release(self, pool: str, instance: ManagedInstance) -> None:
        """Return *instance* to idle state and notify waiters."""
        async with self._condition:
            instance.state = "idle"
            self._condition.notify_all()

    async def mark_dead(self, pool: str, instance: ManagedInstance) -> None:
        """Mark *instance* as dead and notify waiters so drains unblock."""
        async with self._condition:
            instance.state = "dead"
            self._condition.notify_all()

    # ------------------------------------------------------------------
    # Back-pressure check (claim loop uses this before claiming)
    # ------------------------------------------------------------------

    def has_capacity(self, pool: str) -> bool:
        """True if the pool has at least one idle instance."""
        return any(i.state == "idle" for i in self._instances.get(pool, []))

    # ------------------------------------------------------------------
    # Config accessors
    # ------------------------------------------------------------------

    def task_timeout_s(self, pool: str) -> float:
        """Task execution timeout for *pool*."""
        cfg = self._pool_configs.get(pool)
        return cfg.task_timeout_s if cfg else 300.0

    # ------------------------------------------------------------------
    # Drain control (used by ContainerSupervisor.drain)
    # ------------------------------------------------------------------

    def set_draining(self, pool: str, *, draining: bool) -> None:
        """Set the draining flag for *pool*.

        When draining, ``acquire_idle`` returns None immediately instead of
        blocking, which prevents new tasks from being dispatched to the pool.
        Notifies any blocked waiters.
        """
        self._draining[pool] = draining
        # Wake blocked acquires so they can observe the draining flag.
        if draining:
            asyncio.get_event_loop().call_soon(self._notify_all)

    def _notify_all(self) -> None:
        """Fire a notify_all on the condition from a synchronous context."""

        async def _do() -> None:
            async with self._condition:
                self._condition.notify_all()

        asyncio.ensure_future(_do())  # noqa: RUF006

    def is_draining(self, pool: str) -> bool:
        """True if the pool is in draining state."""
        return self._draining.get(pool, False)
