"""Tests for ProgressStore protocol on InMemoryTaskQueue."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from monet.queue import InMemoryTaskQueue, ProgressStore, TaskRecord, TaskStatus
from monet.types import AgentResult, AgentRunContext


def _ctx(agent_id: str = "a", command: str = "fast") -> AgentRunContext:
    return AgentRunContext(
        task="",
        context=[],
        command=command,
        trace_id="t",
        run_id="r",
        agent_id=agent_id,
        skills=[],
    )


def _make_task(
    agent_id: str = "a", command: str = "fast", pool: str = "local"
) -> TaskRecord:
    return {
        "schema_version": 1,
        "task_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "command": command,
        "pool": pool,
        "context": _ctx(agent_id=agent_id, command=command),
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": datetime.now(UTC).isoformat(),
        "claimed_at": None,
        "completed_at": None,
    }


# --- Protocol conformance ---


def test_inmemory_implements_progress_store() -> None:
    q = InMemoryTaskQueue()
    assert isinstance(q, ProgressStore)


# --- Round-trip ---


async def test_publish_get_history_round_trip() -> None:
    q = InMemoryTaskQueue()
    task_id = "task-1"

    await q.publish_progress(task_id, {"agent": "writer", "status": "running"})
    await q.publish_progress(task_id, {"agent": "writer", "status": "done"})

    history = await q.get_progress_history(task_id)
    assert len(history) == 2
    assert history[0]["agent"] == "writer"
    assert history[0]["status"] == "running"
    assert history[0]["v"] == "1"
    assert "ts" in history[0]
    assert history[1]["status"] == "done"


async def test_get_history_returns_snapshot_copy() -> None:
    q = InMemoryTaskQueue()
    await q.publish_progress("t1", {"status": "a"})
    h1 = await q.get_progress_history("t1")
    h1.clear()
    h2 = await q.get_progress_history("t1")
    assert len(h2) == 1


async def test_get_history_empty_for_unknown_id() -> None:
    q = InMemoryTaskQueue()
    history = await q.get_progress_history("nonexistent")
    assert history == []


# --- MAXLEN trimming ---


async def test_maxlen_trimming() -> None:
    q = InMemoryTaskQueue(progress_maxlen=100)
    task_id = "trim-test"

    for i in range(150):
        await q.publish_progress(task_id, {"i": i})

    history = await q.get_progress_history(task_id)
    assert len(history) == 100
    assert history[0]["i"] == 50
    assert history[-1]["i"] == 149


# --- count parameter ---


async def test_get_history_count_limits_results() -> None:
    q = InMemoryTaskQueue()
    for i in range(20):
        await q.publish_progress("t1", {"i": i})

    history = await q.get_progress_history("t1", count=5)
    assert len(history) == 5
    assert history[0]["i"] == 0


# --- Eviction on prune ---


async def test_progress_evicted_with_task_prune() -> None:
    q = InMemoryTaskQueue(completion_ttl_seconds=0.0)
    task = _make_task()
    task_id = task["task_id"]

    await q.enqueue(task)
    await q.publish_progress(task_id, {"status": "work"})
    await q.complete(task_id, AgentResult(success=True, output="ok"))

    # Force prune by enqueueing another task (triggers prune cycle).
    await q.enqueue(_make_task())

    history = await q.get_progress_history(task_id)
    assert history == []


async def test_secondary_key_pruned_with_task() -> None:
    q = InMemoryTaskQueue(completion_ttl_seconds=0.0)
    task = _make_task()
    task_id = task["task_id"]
    run_id = "lg-run-id-for-prune"

    await q.enqueue(task)
    await q.publish_progress(task_id, {"status": "work", "run_id": run_id})
    await q.complete(task_id, AgentResult(success=True, output="ok"))

    # Both indexes populated before prune.
    assert len(await q.get_progress_history(run_id)) == 1

    # Force prune.
    await q.enqueue(_make_task())

    assert await q.get_progress_history(task_id) == []
    assert await q.get_progress_history(run_id) == []
    assert run_id not in q._progress_history


async def test_run_id_equals_task_id_no_duplicate() -> None:
    q = InMemoryTaskQueue()
    task_id = "same-id"

    # When run_id == task_id the dual-index guard must produce only one entry.
    await q.publish_progress(task_id, {"status": "ok", "run_id": task_id})

    assert len(q._progress_history) == 1
    assert task_id not in q._secondary_keys


# --- expire_progress is no-op ---


async def test_expire_progress_noop() -> None:
    q = InMemoryTaskQueue()
    await q.expire_progress("any-id", 3600)


# --- Dual-index by run_id ---


async def test_publish_with_run_id_dual_indexes() -> None:
    q = InMemoryTaskQueue()
    task_id = "task-dual"
    run_id = "run-dual"

    await q.publish_progress(task_id, {"status": "ok", "run_id": run_id})

    task_history = await q.get_progress_history(task_id)
    run_history = await q.get_progress_history(run_id)
    assert len(task_history) == 1
    assert len(run_history) == 1
    assert run_history[0]["status"] == "ok"


async def test_publish_without_run_id_single_index() -> None:
    q = InMemoryTaskQueue()
    task_id = "task-single"

    await q.publish_progress(task_id, {"status": "ok"})

    task_history = await q.get_progress_history(task_id)
    assert len(task_history) == 1
    # No run_id field — no second key written.
    assert len(q._progress_history) == 1


# --- Fan-out still works alongside persistence ---


async def test_publish_still_fans_out_to_subscribers() -> None:
    import asyncio

    q = InMemoryTaskQueue()
    task = _make_task()
    task_id = task["task_id"]
    await q.enqueue(task)

    events: list[dict[str, Any]] = []

    async def consumer() -> None:
        async for ev in q.subscribe_progress(task_id):
            events.append(ev)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    await q.publish_progress(task_id, {"status": "running"})
    await q.complete(task_id, AgentResult(success=True, output="done"))
    await asyncio.wait_for(consumer_task, timeout=2.0)

    assert {"status": "running"} in events
    history = await q.get_progress_history(task_id)
    assert len(history) == 1
    assert history[0]["status"] == "running"
