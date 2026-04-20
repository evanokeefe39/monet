"""Regression tests for InMemoryTaskQueue.await_completion TTL semantics.

Verifies that:
- Double-await within TTL returns the same result (no KeyError on second call).
- After TTL expiry, await_completion raises AwaitAlreadyConsumedError.
- A task that was never enqueued still raises KeyError (not AwaitAlreadyConsumedError).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from monet.queue import AwaitAlreadyConsumedError
from monet.queue.backends.memory import InMemoryTaskQueue
from monet.types import AgentResult
from tests.conftest import make_ctx


def _make_result(run_id: str = "run-1") -> AgentResult:
    return AgentResult(
        success=True,
        output="ok",
        signals=(),
        trace_id="",
        run_id=run_id,
    )


def _make_task(task_id: str, pool: str = "local") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_id": task_id,
        "agent_id": "test",
        "command": "fast",
        "pool": pool,
        "context": make_ctx(run_id="run-1"),
        "status": "pending",
        "result": None,
        "created_at": "2024-01-01T00:00:00Z",
        "claimed_at": None,
        "completed_at": None,
    }


async def test_double_await_within_ttl_returns_same_result() -> None:
    """Second await_completion within TTL must succeed, not raise."""
    q = InMemoryTaskQueue(completion_ttl_seconds=60.0)
    task_id = "task-double"
    await q.enqueue(_make_task(task_id))
    result = _make_result()

    await q.complete(task_id, result)

    r1 = await q.await_completion(task_id, timeout=1.0)
    r2 = await q.await_completion(task_id, timeout=1.0)
    assert r1.success is True
    assert r2.success is True


async def test_await_already_consumed_after_ttl_expiry() -> None:
    """After TTL expires, await_completion raises AwaitAlreadyConsumedError."""
    q = InMemoryTaskQueue(completion_ttl_seconds=0.01)  # 10 ms TTL for fast test
    task_id = "task-expired"
    await q.enqueue(_make_task(task_id))
    await q.complete(task_id, _make_result())

    # First call succeeds.
    await q.await_completion(task_id, timeout=1.0)

    # Wait for TTL to elapse.
    time.sleep(0.02)

    # Second call after TTL must raise AwaitAlreadyConsumedError, not KeyError.
    with pytest.raises(AwaitAlreadyConsumedError):
        await q.await_completion(task_id, timeout=1.0)


async def test_unknown_task_id_raises_key_error() -> None:
    """A task_id that was never enqueued raises KeyError."""
    q = InMemoryTaskQueue()
    with pytest.raises(KeyError):
        await q.await_completion("nonexistent-task", timeout=1.0)


async def test_pruned_ids_distinct_from_unknown() -> None:
    """Pruned (expired) task_id and unknown task_id produce different exceptions."""
    q = InMemoryTaskQueue(completion_ttl_seconds=0.01)
    task_id = "task-prune"
    await q.enqueue(_make_task(task_id))
    await q.complete(task_id, _make_result())

    # First await succeeds.
    await q.await_completion(task_id, timeout=1.0)

    # Wait for TTL.
    time.sleep(0.02)

    # Expired → AwaitAlreadyConsumedError.
    with pytest.raises(AwaitAlreadyConsumedError):
        await q.await_completion(task_id, timeout=1.0)

    # Never existed → KeyError.
    with pytest.raises(KeyError):
        await q.await_completion("ghost-task", timeout=1.0)


async def test_failed_task_double_await_within_ttl() -> None:
    """Double-await on a failed task also succeeds within TTL."""
    q = InMemoryTaskQueue(completion_ttl_seconds=60.0)
    task_id = "task-fail-double"
    await q.enqueue(_make_task(task_id))
    await q.fail(task_id, "simulated error")

    r1 = await q.await_completion(task_id, timeout=1.0)
    r2 = await q.await_completion(task_id, timeout=1.0)
    assert r1.success is False
    assert r2.success is False
