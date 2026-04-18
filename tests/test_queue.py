"""Tests for the task queue protocol, in-memory implementation, and worker."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from monet.core.registry import LocalRegistry
from monet.orchestration._invoke import wait_completion
from monet.queue import InMemoryTaskQueue, TaskRecord, TaskStatus, run_worker
from monet.types import AgentResult, AgentRunContext, SignalType


def _make_ctx(agent_id: str = "test-agent", command: str = "fast") -> AgentRunContext:
    return AgentRunContext(
        task="do something",
        context=[],
        command=command,
        trace_id="t-1",
        run_id="r-1",
        agent_id=agent_id,
        skills=[],
    )


def _make_task(
    agent_id: str = "test-agent", command: str = "fast", pool: str = "local"
) -> TaskRecord:
    ctx = _make_ctx(agent_id=agent_id, command=command)
    return {
        "task_id": str(uuid.uuid4()),
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


# --- InMemoryTaskQueue ---


async def test_enqueue_claim_complete_cycle() -> None:
    q = InMemoryTaskQueue()
    task = _make_task()
    task_id = await q.enqueue(task)
    assert task_id == task["task_id"]

    record = await q.claim("local", consumer_id="c1", block_ms=100)
    assert record is not None
    assert record["task_id"] == task_id
    assert record["status"] == TaskStatus.CLAIMED
    assert record["pool"] == "local"

    result = AgentResult(success=True, output="done", trace_id="t-1", run_id="r-1")
    await q.complete(task_id, result)

    polled = await wait_completion(q, task_id, timeout=1.0)
    assert polled.success is True
    assert polled.output == "done"


async def test_wait_completion_timeout() -> None:
    q = InMemoryTaskQueue()
    task = _make_task()
    task_id = await q.enqueue(task)

    with pytest.raises(TimeoutError):
        await wait_completion(q, task_id, timeout=0.05)


async def test_wait_completion_unknown_task_id() -> None:
    q = InMemoryTaskQueue()
    with pytest.raises(KeyError, match="Unknown task_id"):
        await wait_completion(q, "nonexistent", timeout=0.1)


async def test_enqueue_duplicate_task_id_raises() -> None:
    q = InMemoryTaskQueue()
    task = _make_task()
    await q.enqueue(task)
    with pytest.raises(ValueError, match="Duplicate task_id"):
        await q.enqueue(task)


async def test_claim_returns_none_when_empty() -> None:
    q = InMemoryTaskQueue()
    record = await q.claim("local", consumer_id="c1", block_ms=50)
    assert record is None


async def test_claim_by_pool_isolation() -> None:
    """Tasks in pool A are not visible to workers polling pool B."""
    q = InMemoryTaskQueue()
    await q.enqueue(_make_task(agent_id="agent-a", pool="cloud"))

    # Claim for local pool — should return None after block_ms timeout
    record = await q.claim("local", consumer_id="c1", block_ms=50)
    assert record is None

    # Claim for cloud pool — should get it
    record = await q.claim("cloud", consumer_id="c1", block_ms=50)
    assert record is not None
    assert record["agent_id"] == "agent-a"
    assert record["pool"] == "cloud"


async def test_fail_posts_error_result() -> None:
    q = InMemoryTaskQueue()
    task = _make_task()
    task_id = await q.enqueue(task)
    record = await q.claim("local", consumer_id="c1", block_ms=100)
    assert record is not None

    await q.fail(task_id, "something went wrong")
    result = await wait_completion(q, task_id, timeout=1.0)
    assert result.success is False
    assert result.has_signal(SignalType.SEMANTIC_ERROR)


async def test_wait_completion_cleans_up_memory() -> None:
    """After wait_completion consumes a result, internal state is freed."""
    q = InMemoryTaskQueue()
    task = _make_task(agent_id="agent")
    task_id = await q.enqueue(task)
    record = await q.claim("local", consumer_id="c1", block_ms=100)
    assert record is not None
    await q.complete(task_id, AgentResult(success=True, trace_id="t", run_id="r"))
    await wait_completion(q, task_id, timeout=1.0)

    assert task_id not in q._tasks
    assert task_id not in q._completions


async def test_concurrent_producers_consumers() -> None:
    q = InMemoryTaskQueue()
    n_tasks = 10
    results: list[AgentResult] = []

    async def producer() -> list[str]:
        ids = []
        for _i in range(n_tasks):
            tid = await q.enqueue(_make_task(agent_id="agent"))
            ids.append(tid)
        return ids

    async def consumer() -> None:
        completed = 0
        while completed < n_tasks:
            record = await q.claim("local", consumer_id="c1", block_ms=50)
            if record is None:
                continue
            result = AgentResult(
                success=True,
                output=f"result-{record['task_id'][:8]}",
                trace_id="t-1",
                run_id="r-1",
            )
            await q.complete(record["task_id"], result)
            completed += 1

    task_ids = await producer()
    await consumer()

    for tid in task_ids:
        r = await wait_completion(q, tid, timeout=1.0)
        results.append(r)
        assert r.success is True

    assert len(results) == n_tasks


# --- Worker ---


async def test_worker_executes_agent_through_queue() -> None:
    q = InMemoryTaskQueue()
    registry = LocalRegistry()

    async def handler(ctx: AgentRunContext) -> AgentResult:
        return AgentResult(
            success=True,
            output=f"handled: {ctx['task']}",
            trace_id=ctx["trace_id"],
            run_id=ctx["run_id"],
        )

    registry.register("test-agent", "fast", handler)

    worker_task = asyncio.create_task(run_worker(q, registry))

    try:
        task_id = await q.enqueue(_make_task())
        result = await wait_completion(q, task_id, timeout=2.0)
        assert result.success is True
        assert result.output == "handled: do something"
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task


async def test_worker_fails_task_when_handler_missing() -> None:
    q = InMemoryTaskQueue()
    registry = LocalRegistry()

    task_id = await q.enqueue(_make_task(agent_id="ghost"))

    worker_task = asyncio.create_task(run_worker(q, registry))

    try:
        result = await wait_completion(q, task_id, timeout=2.0)
        assert result.success is False
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task


async def test_worker_handles_handler_exception() -> None:
    q = InMemoryTaskQueue()
    registry = LocalRegistry()

    async def bad_handler(ctx: AgentRunContext) -> AgentResult:
        msg = "agent crashed"
        raise RuntimeError(msg)

    registry.register("crasher", "fast", bad_handler)

    worker_task = asyncio.create_task(run_worker(q, registry))

    try:
        task_id = await q.enqueue(_make_task(agent_id="crasher"))
        result = await wait_completion(q, task_id, timeout=2.0)
        assert result.success is False
        assert any("agent crashed" in s["reason"] for s in result.signals)
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task


async def test_worker_concurrent_execution() -> None:
    """Worker executes multiple tasks concurrently, not sequentially."""
    q = InMemoryTaskQueue()
    registry = LocalRegistry()
    execution_order: list[str] = []
    barrier = asyncio.Event()

    async def slow_handler(ctx: AgentRunContext) -> AgentResult:
        execution_order.append(f"start-{ctx['agent_id']}")
        barrier.set()
        await asyncio.sleep(0.05)
        execution_order.append(f"end-{ctx['agent_id']}")
        return AgentResult(
            success=True, output="done", trace_id=ctx["trace_id"], run_id=ctx["run_id"]
        )

    registry.register("slow", "fast", slow_handler)

    worker_task = asyncio.create_task(run_worker(q, registry, max_concurrency=5))

    try:
        ids = []
        for _i in range(3):
            tid = await q.enqueue(_make_task(agent_id="slow"))
            ids.append(tid)

        results = await asyncio.gather(
            *(wait_completion(q, tid, timeout=5.0) for tid in ids)
        )
        assert all(r.success for r in results)

        starts = [e for e in execution_order if e.startswith("start")]
        assert len(starts) == 3
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task


async def test_backpressure_rejects_enqueue() -> None:
    """Queue with max_pending rejects when full."""
    q = InMemoryTaskQueue(max_pending=2)
    await q.enqueue(_make_task(agent_id="a"))
    await q.enqueue(_make_task(agent_id="b"))
    with pytest.raises(RuntimeError, match="backpressure"):
        await q.enqueue(_make_task(agent_id="c"))


async def test_worker_pool_isolation() -> None:
    """Worker only claims tasks from its assigned pool."""
    q = InMemoryTaskQueue()
    registry = LocalRegistry()

    async def handler(ctx: AgentRunContext) -> AgentResult:
        return AgentResult(
            success=True, output="ok", trace_id=ctx["trace_id"], run_id=ctx["run_id"]
        )

    registry.register("agent", "fast", handler)

    worker_task = asyncio.create_task(run_worker(q, registry, pool="local"))

    try:
        cloud_id = await q.enqueue(_make_task(agent_id="agent", pool="cloud"))
        local_id = await q.enqueue(_make_task(agent_id="agent", pool="local"))

        local_result = await wait_completion(q, local_id, timeout=2.0)
        assert local_result.success is True

        with pytest.raises(TimeoutError):
            await wait_completion(q, cloud_id, timeout=0.2)
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task
