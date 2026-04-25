"""Tests for lease lifecycle: renew_lease, cancel, heartbeat protocol."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from monet.queue._interface import QueueMaintenance
from monet.queue.backends.memory import InMemoryTaskQueue


def _make_task(task_id: str = "t-1", pool: str = "local") -> dict[str, Any]:
    from monet.events import TASK_RECORD_SCHEMA_VERSION, TaskStatus

    return {
        "schema_version": TASK_RECORD_SCHEMA_VERSION,
        "task_id": task_id,
        "agent_id": "test-agent",
        "command": "run",
        "pool": pool,
        "context": {
            "run_id": "run-1",
            "thread_id": "thread-1",
            "trace_id": "trace-1",
            "parent_call_id": "",
        },
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "claimed_at": None,
        "completed_at": None,
    }


@pytest.mark.asyncio
async def test_memory_renew_lease_noop_before_claim() -> None:
    q = InMemoryTaskQueue()
    await q.enqueue(_make_task())
    # renew on unclaimed task — no error, no lease recorded
    await q.renew_lease("t-1")
    with q._lease_lock:
        assert "t-1" not in q._leases


@pytest.mark.asyncio
async def test_memory_renew_lease_updates_timestamp() -> None:
    q = InMemoryTaskQueue()
    await q.enqueue(_make_task())
    await q.claim("local", consumer_id="w1", block_ms=0)

    with q._lease_lock:
        first_ts = q._leases["t-1"]

    await asyncio.sleep(0.01)
    await q.renew_lease("t-1")

    with q._lease_lock:
        second_ts = q._leases["t-1"]

    assert second_ts >= first_ts


@pytest.mark.asyncio
async def test_memory_cancel_sets_flag() -> None:
    q = InMemoryTaskQueue()
    await q.enqueue(_make_task())
    await q.claim("local", consumer_id="w1", block_ms=0)

    assert not q.is_cancelled("t-1")
    await q.cancel("t-1")
    assert q.is_cancelled("t-1")


@pytest.mark.asyncio
async def test_memory_cancel_unknown_task_is_noop() -> None:
    q = InMemoryTaskQueue()
    await q.cancel("nonexistent")
    with q._lease_lock:
        assert "nonexistent" not in q._cancelled


@pytest.mark.asyncio
async def test_memory_complete_clears_lease_and_cancel() -> None:
    from monet.types import AgentResult

    q = InMemoryTaskQueue()
    await q.enqueue(_make_task())
    await q.claim("local", consumer_id="w1", block_ms=0)
    await q.cancel("t-1")

    result = AgentResult(
        success=True,
        output="done",
        signals=(),
        trace_id="trace-1",
        run_id="run-1",
    )
    await q.complete("t-1", result)

    with q._lease_lock:
        assert "t-1" not in q._leases
        assert "t-1" not in q._cancelled


@pytest.mark.asyncio
async def test_memory_fail_clears_lease() -> None:
    q = InMemoryTaskQueue()
    await q.enqueue(_make_task())
    await q.claim("local", consumer_id="w1", block_ms=0)

    with q._lease_lock:
        assert "t-1" in q._leases

    await q.fail("t-1", "something went wrong")

    with q._lease_lock:
        assert "t-1" not in q._leases


def test_memory_queue_does_not_implement_queue_maintenance() -> None:
    """InMemoryTaskQueue lacks lease_ttl_seconds + reclaim_expired."""
    q = InMemoryTaskQueue()
    assert not isinstance(q, QueueMaintenance)
