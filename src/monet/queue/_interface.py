"""Task queue protocol for decoupling orchestration from execution.

Producer side (orchestration builds a TaskRecord, calls enqueue).
Consumer side (workers claim, execute, complete / fail).
Progress fan-out via publish_progress / subscribe_progress.

monet ships one reference implementation (``RedisStreamsTaskQueue``).
Self-hosters with different operational requirements may implement this
protocol against Kafka, RabbitMQ, SQS, or any other transport — the
protocol is intentionally minimal and transport-neutral. Implementations
are free to choose how they track leases, dedupe completions, and handle
crash recovery; the protocol does not mandate Redis-specific machinery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monet.events._tasks import TaskRecord
    from monet.types import AgentResult

__all__ = [
    "AwaitAlreadyConsumedError",
    "ProgressStore",
    "QueueMaintenance",
    "TaskQueue",
]


class AwaitAlreadyConsumedError(Exception):
    """Raised when a completed task result is accessed after TTL expiry.

    The in-memory backend retains completed-task results for a configurable
    TTL (``MONET_QUEUE_COMPLETION_TTL``, default 600 s). A second
    ``_await_completion`` call within that window returns the cached result.
    After the TTL, the record is pruned and this exception is raised to
    distinguish "expired" from "never existed" (``KeyError``).
    """


@runtime_checkable
class TaskQueue(Protocol):
    """Queue interface. Six methods, transport-neutral."""

    async def enqueue(self, task: TaskRecord) -> str:
        """Submit a task. Returns the opaque task_id (usually ``task["task_id"]``).

        The caller builds the TaskRecord (including ``task_id``) before
        dispatch — required so callers that derive per-task tokens from
        task_id have a stable identity before the task lands in the queue.
        """
        ...

    async def claim(
        self, pool: str, consumer_id: str, block_ms: int
    ) -> TaskRecord | None:
        """Claim the next pending task in the pool.

        Blocks up to ``block_ms`` milliseconds waiting for work. Returns
        ``None`` if nothing arrives. ``consumer_id`` identifies the
        claiming worker for crash-recovery ownership (implementations may
        ignore it if they do not need it).
        """
        ...

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Post a successful result for a claimed task.

        Implementations acknowledge the claim internally (e.g. XACK on
        Streams). The protocol does not expose a separate ack step.
        """
        ...

    async def fail(self, task_id: str, error: str) -> None:
        """Post a failure for a claimed task. Ack handled internally."""
        ...

    async def publish_progress(self, task_id: str, event: dict[str, Any]) -> None:
        """Publish a progress event for a task.

        Best-effort — implementations must NOT raise on backpressure,
        serialisation errors, or transport failures.
        """
        ...

    def subscribe_progress(self, task_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield progress events for a task until it completes."""
        ...

    async def await_completion(self, task_id: str, timeout: float) -> AgentResult:
        """Block until task completes or times out.

        Raises:
            TimeoutError: if deadline exceeded.
            KeyError: if task_id was never enqueued.
            AwaitAlreadyConsumedError: if result TTL has expired.
        """
        ...

    async def ping(self) -> bool:
        """Check backend connectivity. Returns True if healthy.

        In-memory backends always return True. Network-backed
        implementations verify the transport is reachable.
        """
        ...

    @property
    def backend_name(self) -> str:
        """Human-readable backend identifier (e.g. 'redis', 'memory').

        Used in health responses and logging without isinstance checks.
        """
        ...

    async def close(self) -> None:
        """Release connections and background resources.

        No-op for in-memory backends. Network-backed implementations
        close connection pools.
        """
        ...


@runtime_checkable
class QueueMaintenance(Protocol):
    """Optional maintenance operations for persistent queue backends.

    Not part of the core transport-neutral contract. Backends that
    support lease-based crash recovery implement this protocol. Server
    lifespans check ``isinstance(queue, QueueMaintenance)`` to activate
    the reclaim sweeper without coupling to a specific backend class.
    """

    @property
    def lease_ttl_seconds(self) -> float:
        """Lease duration for claimed tasks. Used to derive sweep intervals."""
        ...

    async def reclaim_expired(self) -> list[str]:
        """Reclaim tasks whose lease has expired. Returns reclaimed task_ids."""
        ...

    async def renew_lease(self, task_id: str) -> None:
        """Renew the lease for a claimed task.

        Called by the worker heartbeat loop. Implementations record the
        current timestamp so the reclaim sweeper does not evict active
        tasks. No-op on unknown task_ids (task may have already completed).
        """
        ...

    async def cancel(self, task_id: str) -> None:
        """Mark a task as cancelled.

        Workers check this flag before each tool boundary. The flag
        persists until the task reaches a terminal state so late-arriving
        cancel signals are handled correctly.
        """
        ...


@runtime_checkable
class ProgressStore(Protocol):
    """Optional capability: persistent progress retrieval.

    Backends that persist progress events (e.g. Redis Streams) implement
    this to enable historical replay. Checked via ``isinstance`` at the
    server layer. Follows the :class:`QueueMaintenance` precedent.
    """

    async def publish_progress(self, task_id: str, event: dict[str, Any]) -> None:
        """Persist a progress event for the given task."""
        ...

    async def get_progress_history(
        self, run_id: str, *, count: int = 1000
    ) -> list[dict[str, Any]]:
        """Return stored progress events for a run, oldest-first."""
        ...

    async def get_thread_progress_history(
        self, thread_id: str, *, count: int = 1000
    ) -> list[dict[str, Any]]:
        """Return stored progress events for an entire thread, oldest-first."""
        ...

    async def expire_progress(self, run_id: str, ttl: int) -> None:
        """Set a TTL on the progress stream for a completed run."""
        ...
