"""In-memory task queue for testing and monolith-mode deployment.

Uses per-pool ``asyncio.Queue`` for O(1) claim and FIFO ordering.
Completion notification via per-task ``asyncio.Event``.
No persistence, no external deps.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from monet.queue import TaskRecord, TaskStatus
from monet.signals import SignalType
from monet.types import AgentResult, AgentRunContext, Signal

__all__ = ["InMemoryTaskQueue"]


class InMemoryTaskQueue:
    """In-memory task queue backed by asyncio primitives.

    Per-pool queues ensure O(1) claim and preserve FIFO ordering
    within each pool. Workers claim by pool name only (Prefect model).
    """

    # Default max pending tasks across all pools. 0 = unlimited.
    DEFAULT_MAX_PENDING = 0

    def __init__(self, max_pending: int = 0) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._pool_queues: dict[str, asyncio.Queue[str]] = defaultdict(asyncio.Queue)
        self._completions: dict[str, asyncio.Event] = {}
        self._max_pending = max_pending or self.DEFAULT_MAX_PENDING

    @property
    def pending_count(self) -> int:
        """Number of tasks in PENDING state across all pools."""
        return sum(
            1
            for t in self._tasks.values()
            if t["status"] == TaskStatus.PENDING
        )

    async def enqueue(
        self,
        agent_id: str,
        command: str,
        ctx: AgentRunContext,
        pool: str = "local",
    ) -> str:
        """Submit a task. Routes to the pool's internal queue.

        Raises:
            RuntimeError: if max_pending is set and exceeded.
        """
        if self._max_pending and self.pending_count >= self._max_pending:
            msg = (
                f"Queue backpressure: {self.pending_count} pending tasks "
                f"(max {self._max_pending})"
            )
            raise RuntimeError(msg)
        task_id = str(uuid.uuid4())
        record: TaskRecord = {
            "task_id": task_id,
            "agent_id": agent_id,
            "command": command,
            "pool": pool,
            "context": ctx,
            "status": TaskStatus.PENDING,
            "result": None,
            "created_at": datetime.now(UTC).isoformat(),
            "claimed_at": None,
            "completed_at": None,
        }
        self._tasks[task_id] = record
        self._completions[task_id] = asyncio.Event()
        await self._pool_queues[pool].put(task_id)
        return task_id

    async def poll_result(self, task_id: str, timeout: float) -> AgentResult:
        """Block until task completes or timeout.

        Cleans up internal state after consuming the result.

        Raises:
            TimeoutError: if timeout elapses without completion.
            KeyError: if task_id is unknown.
        """
        if task_id not in self._tasks:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)
        event = self._completions[task_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            msg = f"Task {task_id} did not complete within {timeout}s"
            raise TimeoutError(msg) from None

        record = self._tasks[task_id]
        result = record["result"]

        # Cleanup: task is consumed, free memory
        self._tasks.pop(task_id, None)
        self._completions.pop(task_id, None)

        if result is not None:
            return result
        # Task failed without a result object
        return AgentResult(
            success=False,
            output="",
            signals=(
                Signal(
                    type=SignalType.SEMANTIC_ERROR,
                    reason="Task failed in queue",
                    metadata=None,
                ),
            ),
            trace_id=record["context"]["trace_id"],
            run_id=record["context"]["run_id"],
        )

    async def claim(self, pool: str) -> TaskRecord | None:
        """Claim the next pending task in the given pool.

        Non-blocking: returns None if the pool's queue is empty.
        O(1) lookup — no scanning or re-queuing.
        """
        queue = self._pool_queues[pool]
        try:
            task_id = queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

        record = self._tasks.get(task_id)
        if record is None or record["status"] != TaskStatus.PENDING:
            return None

        record["status"] = TaskStatus.CLAIMED
        record["claimed_at"] = datetime.now(UTC).isoformat()
        return record

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Post a successful result."""
        if task_id not in self._tasks:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)
        record = self._tasks[task_id]
        record["status"] = TaskStatus.COMPLETED
        record["result"] = result
        record["completed_at"] = datetime.now(UTC).isoformat()
        self._completions[task_id].set()

    async def fail(self, task_id: str, error: str) -> None:
        """Post a failure."""
        if task_id not in self._tasks:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)
        record = self._tasks[task_id]
        record["status"] = TaskStatus.FAILED
        record["result"] = AgentResult(
            success=False,
            output="",
            signals=(
                Signal(
                    type=SignalType.SEMANTIC_ERROR,
                    reason=error,
                    metadata=None,
                ),
            ),
            trace_id=record["context"]["trace_id"],
            run_id=record["context"]["run_id"],
        )
        record["completed_at"] = datetime.now(UTC).isoformat()
        self._completions[task_id].set()

    async def cancel(self, task_id: str) -> None:
        """Cancel a pending or claimed task.

        Sets status to CANCELLED and signals completion so poll_result
        unblocks. If already completed/failed/cancelled, this is a no-op.
        """
        record = self._tasks.get(task_id)
        if record is None:
            return
        if record["status"] in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            return
        record["status"] = TaskStatus.CANCELLED
        record["completed_at"] = datetime.now(UTC).isoformat()
        event = self._completions.get(task_id)
        if event:
            event.set()
