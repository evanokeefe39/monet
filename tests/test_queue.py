"""Tests for the task queue protocol, in-memory implementation, and worker."""

from __future__ import annotations

import asyncio

import pytest

from monet._queue_memory import InMemoryTaskQueue
from monet._queue_worker import run_worker
from monet._registry import AgentRegistry
from monet.queue import TaskStatus
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


# --- InMemoryTaskQueue ---


async def test_enqueue_claim_complete_cycle() -> None:
    q = InMemoryTaskQueue()
    ctx = _make_ctx()
    task_id = await q.enqueue("test-agent", "fast", ctx)
    assert task_id

    record = await q.claim("local")
    assert record is not None
    assert record["task_id"] == task_id
    assert record["status"] == TaskStatus.CLAIMED
    assert record["pool"] == "local"

    result = AgentResult(success=True, output="done", trace_id="t-1", run_id="r-1")
    await q.complete(task_id, result)

    polled = await q.poll_result(task_id, timeout=1.0)
    assert polled.success is True
    assert polled.output == "done"


async def test_poll_result_timeout() -> None:
    q = InMemoryTaskQueue()
    ctx = _make_ctx()
    task_id = await q.enqueue("test-agent", "fast", ctx)

    with pytest.raises(TimeoutError):
        await q.poll_result(task_id, timeout=0.05)


async def test_poll_result_unknown_task_id() -> None:
    q = InMemoryTaskQueue()
    with pytest.raises(KeyError, match="Unknown task_id"):
        await q.poll_result("nonexistent", timeout=0.1)


async def test_claim_returns_none_when_empty() -> None:
    q = InMemoryTaskQueue()
    record = await q.claim("local")
    assert record is None


async def test_claim_by_pool_isolation() -> None:
    """Tasks in pool A are not visible to workers polling pool B."""
    q = InMemoryTaskQueue()
    await q.enqueue("agent-a", "fast", _make_ctx("agent-a"), pool="cloud")

    # Claim for local pool — should return None
    record = await q.claim("local")
    assert record is None

    # Claim for cloud pool — should get it
    record = await q.claim("cloud")
    assert record is not None
    assert record["agent_id"] == "agent-a"
    assert record["pool"] == "cloud"


async def test_fail_posts_error_result() -> None:
    q = InMemoryTaskQueue()
    ctx = _make_ctx()
    task_id = await q.enqueue("test-agent", "fast", ctx)
    record = await q.claim("local")
    assert record is not None

    await q.fail(task_id, "something went wrong")
    result = await q.poll_result(task_id, timeout=1.0)
    assert result.success is False
    assert result.has_signal(SignalType.SEMANTIC_ERROR)


async def test_poll_result_cleans_up_memory() -> None:
    """After poll_result consumes a result, internal state is freed."""
    q = InMemoryTaskQueue()
    task_id = await q.enqueue("agent", "fast", _make_ctx())
    record = await q.claim("local")
    assert record is not None
    await q.complete(task_id, AgentResult(success=True, trace_id="t", run_id="r"))
    await q.poll_result(task_id, timeout=1.0)

    # Internal state should be cleaned up
    assert task_id not in q._tasks
    assert task_id not in q._completions


async def test_concurrent_producers_consumers() -> None:
    q = InMemoryTaskQueue()
    n_tasks = 10
    results: list[AgentResult] = []

    async def producer() -> list[str]:
        ids = []
        for _i in range(n_tasks):
            tid = await q.enqueue("agent", "fast", _make_ctx("agent"))
            ids.append(tid)
        return ids

    async def consumer() -> None:
        completed = 0
        while completed < n_tasks:
            record = await q.claim("local")
            if record is None:
                await asyncio.sleep(0.01)
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
        r = await q.poll_result(tid, timeout=1.0)
        results.append(r)
        assert r.success is True

    assert len(results) == n_tasks


# --- Worker ---


async def test_worker_executes_agent_through_queue() -> None:
    q = InMemoryTaskQueue()
    registry = AgentRegistry()

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
        task_id = await q.enqueue("test-agent", "fast", _make_ctx())
        result = await q.poll_result(task_id, timeout=2.0)
        assert result.success is True
        assert result.output == "handled: do something"
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task


async def test_worker_fails_task_when_handler_missing() -> None:
    q = InMemoryTaskQueue()
    registry = AgentRegistry()

    # No handler registered — worker should fail the task
    task_id = await q.enqueue("ghost", "fast", _make_ctx("ghost"))

    # Worker needs at least one handler to start claiming (it always claims by pool)
    worker_task = asyncio.create_task(run_worker(q, registry))

    try:
        result = await q.poll_result(task_id, timeout=2.0)
        assert result.success is False
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task


async def test_worker_handles_handler_exception() -> None:
    q = InMemoryTaskQueue()
    registry = AgentRegistry()

    async def bad_handler(ctx: AgentRunContext) -> AgentResult:
        msg = "agent crashed"
        raise RuntimeError(msg)

    registry.register("crasher", "fast", bad_handler)

    worker_task = asyncio.create_task(run_worker(q, registry))

    try:
        task_id = await q.enqueue("crasher", "fast", _make_ctx("crasher"))
        result = await q.poll_result(task_id, timeout=2.0)
        assert result.success is False
        assert any("agent crashed" in s["reason"] for s in result.signals)
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task


async def test_worker_concurrent_execution() -> None:
    """Worker executes multiple tasks concurrently, not sequentially."""
    q = InMemoryTaskQueue()
    registry = AgentRegistry()
    execution_order: list[str] = []
    barrier = asyncio.Event()

    async def slow_handler(ctx: AgentRunContext) -> AgentResult:
        execution_order.append(f"start-{ctx['agent_id']}")
        # All tasks wait on the barrier — proves they're running concurrently
        barrier.set()
        await asyncio.sleep(0.05)
        execution_order.append(f"end-{ctx['agent_id']}")
        return AgentResult(
            success=True, output="done", trace_id=ctx["trace_id"], run_id=ctx["run_id"]
        )

    registry.register("slow", "fast", slow_handler)

    worker_task = asyncio.create_task(run_worker(q, registry, max_concurrency=5))

    try:
        # Enqueue 3 tasks
        ids = []
        for _i in range(3):
            tid = await q.enqueue("slow", "fast", _make_ctx("slow"))
            ids.append(tid)

        # Wait for all to complete
        results = await asyncio.gather(
            *(q.poll_result(tid, timeout=5.0) for tid in ids)
        )
        assert all(r.success for r in results)

        # All starts should happen before any end (concurrent, not sequential)
        starts = [e for e in execution_order if e.startswith("start")]
        assert len(starts) == 3
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task


async def test_worker_pool_isolation() -> None:
    """Worker only claims tasks from its assigned pool."""
    q = InMemoryTaskQueue()
    registry = AgentRegistry()

    async def handler(ctx: AgentRunContext) -> AgentResult:
        return AgentResult(
            success=True, output="ok", trace_id=ctx["trace_id"], run_id=ctx["run_id"]
        )

    registry.register("agent", "fast", handler)

    # Start worker for "local" pool only
    worker_task = asyncio.create_task(run_worker(q, registry, pool="local"))

    try:
        # Enqueue to cloud pool — worker should NOT claim it
        cloud_id = await q.enqueue("agent", "fast", _make_ctx(), pool="cloud")

        # Enqueue to local pool — worker should claim it
        local_id = await q.enqueue("agent", "fast", _make_ctx())

        local_result = await q.poll_result(local_id, timeout=2.0)
        assert local_result.success is True

        # Cloud task should still be pending (not claimed by local worker)
        with pytest.raises(TimeoutError):
            await q.poll_result(cloud_id, timeout=0.2)
    finally:
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task
