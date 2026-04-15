"""In-memory task queue for testing and monolith-mode deployment.

Uses per-pool ``asyncio.Queue`` for O(1) claim and FIFO ordering.
Completion notification via per-task ``asyncio.Event``. No persistence,
no external deps. Rejected by boot validation when ``REDIS_URI`` is set.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from monet.queue import TaskRecord, TaskStatus
from monet.signals import SignalType
from monet.types import AgentResult, Signal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ["InMemoryTaskQueue"]

_TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.FAILED)

# Subscriber queue size. Drops on full — progress is best-effort.
_SUBSCRIBER_QUEUE_MAX = 64


class InMemoryTaskQueue:
    """In-memory task queue backed by asyncio primitives.

    Per-pool queues ensure O(1) claim and FIFO ordering within each pool.
    Implements the six-method ``TaskQueue`` protocol plus a private
    ``_await_completion`` used by ``wait_completion`` in orchestration
    (isinstance-dispatched; not part of the protocol).
    """

    DEFAULT_MAX_PENDING = 0

    def __init__(self, max_pending: int = 0) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._pool_queues: dict[str, asyncio.Queue[str]] = defaultdict(asyncio.Queue)
        self._completions: dict[str, asyncio.Event] = {}
        self._max_pending = max_pending or self.DEFAULT_MAX_PENDING
        self._progress_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = (
            defaultdict(set)
        )

    @property
    def pending_count(self) -> int:
        """Number of tasks in PENDING state across all pools."""
        return sum(1 for t in self._tasks.values() if t["status"] == TaskStatus.PENDING)

    async def enqueue(self, task: TaskRecord) -> str:
        """Submit a pre-built TaskRecord. Raises on backpressure or duplicate id."""
        if self._max_pending and self.pending_count >= self._max_pending:
            msg = (
                f"Queue backpressure: {self.pending_count} pending tasks "
                f"(max {self._max_pending})"
            )
            raise RuntimeError(msg)
        task_id = task["task_id"]
        if task_id in self._tasks:
            msg = f"Duplicate task_id: {task_id}"
            raise ValueError(msg)
        # Store a defensive copy so callers mutating the input after
        # enqueue do not corrupt queue state.
        record: TaskRecord = {**task, "status": TaskStatus.PENDING}
        self._tasks[task_id] = record
        self._completions[task_id] = asyncio.Event()
        await self._pool_queues[task["pool"]].put(task_id)
        return task_id

    async def claim(
        self, pool: str, consumer_id: str, block_ms: int
    ) -> TaskRecord | None:
        """Claim the next pending task in the pool.

        Blocks up to ``block_ms`` milliseconds. ``block_ms <= 0`` means
        non-blocking (immediate ``None`` if empty). ``consumer_id`` is
        accepted for protocol conformance but ignored — there is no PEL
        in the in-memory backend, lease reclamation is a Redis concept.
        """
        del consumer_id  # accepted, unused
        queue = self._pool_queues[pool]
        if block_ms <= 0:
            try:
                task_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
        else:
            try:
                task_id = await asyncio.wait_for(queue.get(), timeout=block_ms / 1000.0)
            except TimeoutError:
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

    # --- Progress streaming ---

    async def publish_progress(self, task_id: str, event: dict[str, Any]) -> None:
        """Fan out an event to all subscribers. Drops on full queue."""
        for sub_q in self._progress_subscribers.get(task_id, set()):
            with contextlib.suppress(asyncio.QueueFull):
                sub_q.put_nowait(event)

    async def subscribe_progress(self, task_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield progress events until the task reaches a terminal state."""
        sub_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_MAX
        )
        self._progress_subscribers[task_id].add(sub_q)
        try:
            while True:
                record = self._tasks.get(task_id)
                terminal = record is None or record["status"] in _TERMINAL_STATUSES
                if terminal:
                    await asyncio.sleep(0)
                    while not sub_q.empty():
                        yield sub_q.get_nowait()
                    return
                try:
                    data = await asyncio.wait_for(sub_q.get(), timeout=1.0)
                    yield data
                except TimeoutError:
                    continue
        finally:
            self._progress_subscribers[task_id].discard(sub_q)
            if not self._progress_subscribers[task_id]:
                self._progress_subscribers.pop(task_id, None)

    # --- Private helper for orchestration.wait_completion ---

    async def _await_completion(self, task_id: str, timeout: float) -> AgentResult:
        """Block until the task is completed or failed.

        Isinstance-dispatched from ``monet.orchestration._invoke.wait_completion``.
        Not part of the public protocol — backends implement completion
        notification however suits their transport.

        Raises:
            TimeoutError: if ``timeout`` seconds elapse without a result.
            KeyError: if ``task_id`` is unknown.
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

        self._tasks.pop(task_id, None)
        self._completions.pop(task_id, None)

        if result is not None:
            return result
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
