"""In-memory task queue for testing and monolith-mode deployment.

Uses per-pool ``asyncio.Queue`` for O(1) claim and FIFO ordering.
Completion notification via per-task ``asyncio.Event``. No persistence,
no external deps. Rejected by boot validation when ``REDIS_URI`` is set.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from monet.queue import TaskRecord, TaskStatus
from monet.queue._interface import AwaitAlreadyConsumedError
from monet.signals import SignalType
from monet.types import AgentResult, Signal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = logging.getLogger("monet.queue.memory")

__all__ = ["InMemoryTaskQueue"]

_TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.FAILED)

# Subscriber queue size. Drops on full — progress is best-effort.
_SUBSCRIBER_QUEUE_MAX = 64

_DEFAULT_COMPLETION_TTL = 600.0
_DEFAULT_PROGRESS_MAXLEN = 1000
_MAX_PRUNED_IDS = 10_000


class InMemoryTaskQueue:
    """In-memory task queue backed by asyncio primitives.

    Per-pool queues ensure O(1) claim and FIFO ordering within each pool.
    Implements the six-method ``TaskQueue`` protocol plus a private
    ``await_completion`` used by ``wait_completion`` in orchestration
    (isinstance-dispatched; not part of the protocol).

    Completed-task results are retained for ``completion_ttl_seconds``
    (default 600 s) so a second ``await_completion`` call within that
    window returns the cached result. After the TTL, the record is pruned
    and ``AwaitAlreadyConsumedError`` is raised to distinguish "expired" from
    "never existed" (``KeyError``).
    """

    DEFAULT_MAX_PENDING = 0

    def __init__(
        self,
        max_pending: int = 0,
        completion_ttl_seconds: float = _DEFAULT_COMPLETION_TTL,
        progress_maxlen: int = _DEFAULT_PROGRESS_MAXLEN,
    ) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._pool_queues: dict[str, asyncio.Queue[str]] = defaultdict(asyncio.Queue)
        self._completions: dict[str, asyncio.Event] = {}
        self._max_pending = max_pending or self.DEFAULT_MAX_PENDING
        self._progress_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = (
            defaultdict(set)
        )
        self._completion_ttl_seconds = completion_ttl_seconds
        self._progress_maxlen = progress_maxlen
        self._progress_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # Maps task_id → monotonic timestamp when the task reached a terminal state.
        self._completion_times: dict[str, float] = {}
        # Task IDs pruned after TTL expiry — distinguishes "expired" from "unknown".
        self._pruned_ids: set[str] = set()
        # Maps task_id → set of secondary keys (run_ids) written to _progress_history
        # via dual-index. Pruned together with the primary task record.
        self._secondary_keys: dict[str, set[str]] = {}

    @property
    def pending_count(self) -> int:
        """Number of tasks in PENDING state across all pools."""
        return sum(1 for t in self._tasks.values() if t["status"] == TaskStatus.PENDING)

    async def enqueue(self, task: TaskRecord) -> str:
        """Submit a pre-built TaskRecord. Raises on backpressure or duplicate id."""
        self._prune_expired_completions()
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
        _log.info(
            "queue.enqueue pool=%s agent=%s command=%s task=%s pending=%d",
            task["pool"],
            task["agent_id"],
            task["command"],
            task_id,
            self.pending_count,
        )
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
        _log.info(
            "queue.claim pool=%s agent=%s command=%s task=%s",
            pool,
            record["agent_id"],
            record["command"],
            task_id,
        )
        return record

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Post a successful result. Idempotent — skips if already terminal."""
        if task_id not in self._tasks:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)
        record = self._tasks[task_id]
        if record["status"] in _TERMINAL_STATUSES:
            _log.debug("complete() already-terminal task %s, skipping", task_id)
            return
        record["status"] = TaskStatus.COMPLETED
        record["result"] = result
        record["completed_at"] = datetime.now(UTC).isoformat()
        self._completion_times[task_id] = time.monotonic()
        self._completions[task_id].set()
        _log.info(
            "queue.complete task=%s agent=%s command=%s success=%s",
            task_id,
            record["agent_id"],
            record["command"],
            getattr(result, "success", True),
        )

    async def fail(self, task_id: str, error: str) -> None:
        """Post a failure. Idempotent — skips if already terminal."""
        if task_id not in self._tasks:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)
        record = self._tasks[task_id]
        if record["status"] in _TERMINAL_STATUSES:
            _log.debug("fail() already-terminal task %s, skipping", task_id)
            return
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
        self._completion_times[task_id] = time.monotonic()
        self._completions[task_id].set()
        _log.warning(
            "queue.fail task=%s agent=%s command=%s error=%s",
            task_id,
            record["agent_id"],
            record["command"],
            error,
        )

    def _prune_expired_completions(self) -> None:
        """Remove completed-task records that have exceeded the TTL.

        Adds pruned IDs to ``_pruned_ids`` so ``await_completion`` can
        raise ``AwaitAlreadyConsumedError`` instead of ``KeyError`` for callers
        that arrive after expiry.
        """
        now = time.monotonic()
        expired = [
            tid
            for tid, t in self._completion_times.items()
            if now - t > self._completion_ttl_seconds
        ]
        for tid in expired:
            self._tasks.pop(tid, None)
            self._completions.pop(tid, None)
            self._progress_history.pop(tid, None)
            for sk in self._secondary_keys.pop(tid, set()):
                self._progress_history.pop(sk, None)
            del self._completion_times[tid]
            self._pruned_ids.add(tid)
        if len(self._pruned_ids) > _MAX_PRUNED_IDS:
            excess = len(self._pruned_ids) - _MAX_PRUNED_IDS
            for _ in range(excess):
                self._pruned_ids.pop()

    # --- Progress streaming ---

    async def publish_progress(self, task_id: str, event: dict[str, Any]) -> None:
        """Persist and fan out a progress event. Drops on full queue."""
        entry = {"v": "1", "ts": time.monotonic_ns(), **event}
        history = self._progress_history[task_id]
        history.append(entry)
        if len(history) > self._progress_maxlen:
            self._progress_history[task_id] = history[-self._progress_maxlen :]
        run_id = event.get("run_id", "")
        if run_id and run_id != task_id:
            run_history = self._progress_history[run_id]
            run_history.append(entry)
            if len(run_history) > self._progress_maxlen:
                self._progress_history[run_id] = run_history[-self._progress_maxlen :]
            self._secondary_keys.setdefault(task_id, set()).add(run_id)
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

    # --- ProgressStore protocol ---

    async def get_progress_history(
        self, run_id: str, *, count: int = 1000
    ) -> list[dict[str, Any]]:
        """Return a snapshot copy of stored progress events."""
        return list(self._progress_history.get(run_id, []))[:count]

    async def expire_progress(self, run_id: str, ttl: int) -> None:
        """No-op for in-memory backend — eviction handled by prune cycle."""

    # --- Private helper for orchestration.wait_completion ---

    async def await_completion(self, task_id: str, timeout: float) -> AgentResult:
        """Block until the task is completed or failed.

        Isinstance-dispatched from ``monet.orchestration._invoke.wait_completion``.
        Not part of the public protocol — backends implement completion
        notification however suits their transport.

        Results are cached for ``completion_ttl_seconds`` so a second call
        within that window returns the same result. After the TTL, the
        record is pruned and ``AwaitAlreadyConsumedError`` is raised.

        Raises:
            TimeoutError: if ``timeout`` seconds elapse without a result.
            KeyError: if ``task_id`` was never enqueued.
            AwaitAlreadyConsumedError: if ``task_id`` result TTL has expired.
        """
        self._prune_expired_completions()

        if task_id not in self._tasks:
            if task_id in self._pruned_ids:
                msg = f"Task {task_id!r} expired (TTL={self._completion_ttl_seconds}s)"
                raise AwaitAlreadyConsumedError(msg)
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

        # Do NOT pop — result stays cached until TTL prune so a second
        # awaiter within the window reads the same result without error.
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

    async def ping(self) -> bool:
        """In-memory backend is always healthy."""
        return True

    @property
    def backend_name(self) -> str:
        return "memory"

    async def close(self) -> None:
        """No-op for in-memory backend."""
