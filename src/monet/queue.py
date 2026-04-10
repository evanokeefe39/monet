"""Task queue protocol for decoupling orchestration from execution.

The queue has two sides:
- **Producer** (called by ``invoke_agent``): ``enqueue`` + ``poll_result``
- **Consumer** (called by workers): ``claim`` + ``complete`` + ``fail``

Workers claim by pool (Prefect model): a worker registers for one pool
and executes whatever lands in it. Handler lookup is the worker's concern.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from monet.types import AgentResult, AgentRunContext

__all__ = [
    "InMemoryTaskQueue",  # noqa: F822
    "RedisTaskQueue",  # noqa: F822
    "SQLiteTaskQueue",  # noqa: F822
    "TaskQueue",
    "TaskRecord",
    "TaskStatus",
    "UpstashTaskQueue",  # noqa: F822
    "run_worker",  # noqa: F822
]


def __getattr__(name: str) -> Any:
    if name == "InMemoryTaskQueue":
        from monet.core.queue_memory import InMemoryTaskQueue

        return InMemoryTaskQueue
    if name == "SQLiteTaskQueue":
        from monet.core.queue_sqlite import SQLiteTaskQueue

        return SQLiteTaskQueue
    if name == "RedisTaskQueue":
        from monet.core.queue_redis import RedisTaskQueue

        return RedisTaskQueue
    if name == "UpstashTaskQueue":
        from monet.core.queue_upstash import UpstashTaskQueue

        return UpstashTaskQueue
    if name == "run_worker":
        from monet.core.queue_worker import run_worker

        return run_worker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class TaskStatus(StrEnum):
    """Lifecycle states of a queued task."""

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    """Queue interface for task dispatch and consumption.

    Producer side (orchestration):
        enqueue — submit a task with a pool assignment, receive a task_id
        poll_result — block until a task completes or timeout

    Consumer side (workers):
        claim — grab the next pending task in a pool (Prefect model)
        complete — post a successful result
        fail — post a failure
    """

    async def enqueue(
        self,
        agent_id: str,
        command: str,
        ctx: AgentRunContext,
        pool: str = "local",
    ) -> str:
        """Submit a task to the queue.

        Args:
            agent_id: Target agent identifier.
            command: Agent command to invoke.
            ctx: Full agent run context.
            pool: Pool this task belongs to.

        Returns:
            task_id that can be passed to ``poll_result``.
        """
        ...

    async def poll_result(self, task_id: str, timeout: float) -> AgentResult:
        """Block until the task is completed or failed.

        Raises:
            TimeoutError: if ``timeout`` seconds elapse without a result.
            KeyError: if ``task_id`` is unknown.
        """
        ...

    async def claim(self, pool: str) -> TaskRecord | None:
        """Claim the next pending task in the given pool.

        Workers call this in a loop. Returns None if no tasks are
        available in the pool. The worker looks up the handler locally
        and executes — the queue does not filter by capability.

        Returns:
            A TaskRecord with status CLAIMED, or None if nothing available.
        """
        ...

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Post a successful result for a claimed task."""
        ...

    async def fail(self, task_id: str, error: str) -> None:
        """Post a failure for a claimed task."""
        ...

    async def cancel(self, task_id: str) -> None:
        """Cancel a pending or claimed task.

        Workers should check for cancellation and skip execution.
        If the task is already completed or failed, this is a no-op.
        """
        ...
