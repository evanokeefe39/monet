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

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._pool_queues: dict[str, asyncio.Queue[str]] = defaultdict(asyncio.Queue)
        self._completions: dict[str, asyncio.Event] = {}

    async def enqueue(
        self,
        agent_id: str,
        command: str,
        ctx: AgentRunContext,
        pool: str = "local",
    ) -> str:
        """Submit a task. Routes to the pool's internal queue."""
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
