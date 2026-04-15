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

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monet.types import AgentResult, AgentRunContext

__all__ = [
    "TaskQueue",
    "TaskRecord",
    "TaskStatus",
]


class TaskStatus(StrEnum):
    """Lifecycle states of a queued task."""

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskRecord(TypedDict):
    """Snapshot of a task at a point in time."""

    task_id: str
    agent_id: str
    command: str
    pool: str
    context: AgentRunContext
    status: TaskStatus
    result: AgentResult | None
    created_at: str
    claimed_at: str | None
    completed_at: str | None


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
