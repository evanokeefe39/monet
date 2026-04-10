"""Tests for RedisTaskQueue.

Skipped entirely if the ``redis`` package is not installed or if
a Redis server is not reachable at the URL in ``REDIS_URL`` (defaults
to ``redis://localhost:6379``).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

redis_available = False
try:
    import redis.asyncio

    redis_available = True
except ImportError:
    pass

pytestmark = pytest.mark.skipif(
    not redis_available, reason="redis package not installed"
)


def _make_ctx(
    task: str = "test task",
    trace_id: str = "",
    run_id: str = "",
    agent_id: str = "test-agent",
) -> dict:
    """Build a minimal AgentRunContext dict."""
    return {
        "task": task,
        "context": [],
        "command": "run",
        "trace_id": trace_id or str(uuid.uuid4()),
        "run_id": run_id or str(uuid.uuid4()),
        "agent_id": agent_id,
        "skills": [],
    }


@pytest.fixture
async def redis_url():
    """Skip if Redis is not reachable."""
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        r = redis.asyncio.from_url(url)
        await r.ping()
        await r.close()
    except Exception:
        pytest.skip("Redis server not reachable")
    return url


@pytest.fixture
async def queue(redis_url: str):
    """Create a RedisTaskQueue with a unique prefix for test isolation."""
    from monet.core.queue_redis import RedisTaskQueue

    prefix = f"monet_test_{uuid.uuid4().hex[:8]}"
    q = RedisTaskQueue(redis_url, prefix=prefix, lease_ttl=5)
    yield q
    # Cleanup: delete all keys with our prefix.
    client = await q._ensure_client()
    cursor = 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match=f"{prefix}:*", count=200)
        if keys:
            await client.delete(*keys)
        if cursor == 0:
            break
    await q.close()


@pytest.fixture
async def polling_queue(redis_url: str):
    """Create a RedisTaskQueue in polling mode."""
    from monet.core.queue_redis import RedisTaskQueue

    prefix = f"monet_test_{uuid.uuid4().hex[:8]}"
    q = RedisTaskQueue(redis_url, prefix=prefix, use_polling=True)
    yield q
    client = await q._ensure_client()
    cursor = 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match=f"{prefix}:*", count=200)
        if keys:
            await client.delete(*keys)
        if cursor == 0:
            break
    await q.close()


@pytest.mark.asyncio
async def test_enqueue_and_claim(queue) -> None:
    """Basic enqueue + claim cycle returns a valid TaskRecord."""
    from monet.queue import TaskStatus

    ctx = _make_ctx()
    task_id = await queue.enqueue("agent-a", "run", ctx, pool="local")

    record = await queue.claim("local")
    assert record is not None
    assert record["task_id"] == task_id
    assert record["agent_id"] == "agent-a"
    assert record["command"] == "run"
    assert record["pool"] == "local"
    assert record["status"] == TaskStatus.CLAIMED
    assert record["context"]["task"] == "test task"

    # Queue is now empty.
    assert await queue.claim("local") is None


@pytest.mark.asyncio
async def test_poll_result_complete(queue) -> None:
    """poll_result returns after complete() is called."""
    from monet.types import AgentResult

    ctx = _make_ctx()
    task_id = await queue.enqueue("agent-a", "run", ctx)

    async def _worker() -> None:
        await asyncio.sleep(0.1)
        record = await queue.claim("local")
        assert record is not None
        await queue.complete(
            record["task_id"],
            AgentResult(
                success=True,
                output="done",
                trace_id=ctx["trace_id"],
                run_id=ctx["run_id"],
            ),
        )

    task = asyncio.create_task(_worker())
    result = await queue.poll_result(task_id, timeout=5.0)
    assert result.success is True
    assert result.output == "done"
    await task


@pytest.mark.asyncio
async def test_poll_result_timeout(queue) -> None:
    """poll_result raises TimeoutError when no completion arrives."""
    ctx = _make_ctx()
    task_id = await queue.enqueue("agent-a", "run", ctx)

    with pytest.raises(TimeoutError):
        await queue.poll_result(task_id, timeout=0.5)


@pytest.mark.asyncio
async def test_pool_isolation(queue) -> None:
    """Tasks in pool A are not claimable from pool B."""
    ctx = _make_ctx()
    await queue.enqueue("agent-a", "run", ctx, pool="pool-a")

    # Pool B should be empty.
    assert await queue.claim("pool-b") is None

    # Pool A should have the task.
    record = await queue.claim("pool-a")
    assert record is not None
    assert record["pool"] == "pool-a"


@pytest.mark.asyncio
async def test_cancel_pending(queue) -> None:
    """Cancelling a pending task removes it from the queue."""
    ctx = _make_ctx()
    task_id = await queue.enqueue("agent-a", "run", ctx, pool="local")

    await queue.cancel(task_id)

    # Task should no longer be claimable.
    assert await queue.claim("local") is None

    # poll_result should return a failed result (cancelled).
    result = await queue.poll_result(task_id, timeout=1.0)
    assert result.success is False


@pytest.mark.asyncio
async def test_fail_task(queue) -> None:
    """fail() sets error status and unblocks poll_result."""
    ctx = _make_ctx()
    task_id = await queue.enqueue("agent-a", "run", ctx)

    async def _worker() -> None:
        await asyncio.sleep(0.1)
        record = await queue.claim("local")
        assert record is not None
        await queue.fail(record["task_id"], "something broke")

    task = asyncio.create_task(_worker())
    result = await queue.poll_result(task_id, timeout=5.0)
    assert result.success is False
    assert any("something broke" in s["reason"] for s in result.signals)
    await task


@pytest.mark.asyncio
async def test_polling_mode(polling_queue) -> None:
    """poll_result works in polling fallback mode."""
    from monet.types import AgentResult

    ctx = _make_ctx()
    task_id = await polling_queue.enqueue("agent-a", "run", ctx)

    async def _worker() -> None:
        await asyncio.sleep(0.2)
        record = await polling_queue.claim("local")
        assert record is not None
        await polling_queue.complete(
            record["task_id"],
            AgentResult(
                success=True,
                output="polled",
                trace_id=ctx["trace_id"],
                run_id=ctx["run_id"],
            ),
        )

    task = asyncio.create_task(_worker())
    result = await polling_queue.poll_result(task_id, timeout=5.0)
    assert result.success is True
    assert result.output == "polled"
    await task


@pytest.mark.asyncio
async def test_prefix_isolation(redis_url: str) -> None:
    """Two queues with different prefixes don't interfere."""
    from monet.core.queue_redis import RedisTaskQueue

    prefix_a = f"monet_test_{uuid.uuid4().hex[:8]}"
    prefix_b = f"monet_test_{uuid.uuid4().hex[:8]}"
    q_a = RedisTaskQueue(redis_url, prefix=prefix_a)
    q_b = RedisTaskQueue(redis_url, prefix=prefix_b)

    try:
        ctx = _make_ctx()
        await q_a.enqueue("agent-a", "run", ctx, pool="local")
        await q_b.enqueue("agent-b", "run", ctx, pool="local")

        rec_a = await q_a.claim("local")
        rec_b = await q_b.claim("local")

        assert rec_a is not None
        assert rec_b is not None
        assert rec_a["agent_id"] == "agent-a"
        assert rec_b["agent_id"] == "agent-b"

        # Each queue should now be empty.
        assert await q_a.claim("local") is None
        assert await q_b.claim("local") is None
    finally:
        # Cleanup both.
        for q, prefix in [(q_a, prefix_a), (q_b, prefix_b)]:
            client = await q._ensure_client()
            cursor = 0
            while True:
                cursor, keys = await client.scan(
                    cursor=cursor, match=f"{prefix}:*", count=200
                )
                if keys:
                    await client.delete(*keys)
                if cursor == 0:
                    break
            await q.close()
