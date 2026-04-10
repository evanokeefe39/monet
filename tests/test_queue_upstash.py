"""Tests for the Upstash Redis-backed task queue.

Requires:
- upstash-redis package installed
- UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN env vars set
"""

from __future__ import annotations

import os
import uuid

import pytest

upstash_available = False
try:
    from upstash_redis.asyncio import Redis

    upstash_available = True
except ImportError:
    pass

pytestmark = pytest.mark.skipif(
    not upstash_available, reason="upstash-redis not installed"
)


def _make_ctx(agent_id: str = "test-agent", command: str = "fast") -> dict[str, object]:
    """Build a minimal AgentRunContext for testing."""
    from monet.types import AgentRunContext

    return AgentRunContext(
        task="do something",
        context=[],
        command=command,
        trace_id="t-1",
        run_id="r-1",
        agent_id=agent_id,
        skills=[],
    )


def _require_credentials() -> tuple[str, str]:
    """Return (url, token) or skip the test."""
    url = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        pytest.skip("Upstash credentials not configured")
    return url, token


@pytest.fixture
async def queue():  # type: ignore[no-untyped-def]
    """Provide an UpstashTaskQueue with a unique prefix per test."""
    url, token = _require_credentials()
    from monet.core.queue_upstash import UpstashTaskQueue

    # Unique prefix per test run to avoid collisions.
    prefix = f"monet-test-{uuid.uuid4().hex[:8]}"
    q = UpstashTaskQueue(url=url, token=token, prefix=prefix, poll_interval=0.1)
    yield q
    # Cleanup: delete all keys with this prefix.
    redis = Redis(url=url, token=token)
    cursor = "0"
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{prefix}:*", count=100)
        if keys:
            for key in keys:
                await redis.delete(key)
        if cursor == "0" or cursor == 0:
            break


async def test_enqueue_and_claim(queue) -> None:  # type: ignore[no-untyped-def]
    """Enqueue a task and claim it from the correct pool."""
    from monet.queue import TaskStatus

    ctx = _make_ctx()
    task_id = await queue.enqueue("test-agent", "fast", ctx)
    assert task_id

    record = await queue.claim("local")
    assert record is not None
    assert record["task_id"] == task_id
    assert record["status"] == TaskStatus.CLAIMED
    assert record["agent_id"] == "test-agent"
    assert record["pool"] == "local"


async def test_poll_result_complete(queue) -> None:  # type: ignore[no-untyped-def]
    """poll_result returns the result after complete is called."""
    import asyncio

    from monet.types import AgentResult, ArtifactPointer, Signal, SignalType

    ctx = _make_ctx()
    task_id = await queue.enqueue("test-agent", "fast", ctx)
    await queue.claim("local")

    original = AgentResult(
        success=True,
        output={"key": "value"},
        artifacts=(ArtifactPointer(artifact_id="a1", url="s3://bucket/a1"),),
        signals=(
            Signal(
                type=SignalType.LOW_CONFIDENCE,
                reason="not sure",
                metadata={"score": 0.3},
            ),
        ),
        trace_id="t-1",
        run_id="r-1",
    )

    # Complete in a background task so poll_result can find it.
    async def do_complete() -> None:
        await asyncio.sleep(0.05)
        await queue.complete(task_id, original)

    task = asyncio.create_task(do_complete())

    result = await queue.poll_result(task_id, timeout=5.0)
    await task
    assert result.success is True
    assert result.output == {"key": "value"}
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["artifact_id"] == "a1"
    assert len(result.signals) == 1
    assert result.signals[0]["type"] == SignalType.LOW_CONFIDENCE


async def test_poll_result_timeout(queue) -> None:  # type: ignore[no-untyped-def]
    """poll_result raises TimeoutError when task does not complete."""
    ctx = _make_ctx()
    task_id = await queue.enqueue("test-agent", "fast", ctx)

    with pytest.raises(TimeoutError):
        await queue.poll_result(task_id, timeout=0.2)


async def test_pool_isolation(queue) -> None:  # type: ignore[no-untyped-def]
    """Tasks in pool A are not visible to workers polling pool B."""
    ctx = _make_ctx("agent-cloud")
    await queue.enqueue("agent-cloud", "fast", ctx, pool="cloud")

    # Claim from local pool -- should get nothing.
    record = await queue.claim("local")
    assert record is None

    # Claim from cloud pool -- should get it.
    record = await queue.claim("cloud")
    assert record is not None
    assert record["agent_id"] == "agent-cloud"
    assert record["pool"] == "cloud"


async def test_cancel_pending(queue) -> None:  # type: ignore[no-untyped-def]
    """Cancelling a pending task makes poll_result return a failure."""
    ctx = _make_ctx()
    task_id = await queue.enqueue("agent", "fast", ctx)
    await queue.cancel(task_id)

    result = await queue.poll_result(task_id, timeout=1.0)
    assert result.success is False


async def test_fail_task(queue) -> None:  # type: ignore[no-untyped-def]
    """Failing a task sets error signal and marks as failed."""
    from monet.types import SignalType

    ctx = _make_ctx()
    task_id = await queue.enqueue("test-agent", "fast", ctx)
    await queue.claim("local")

    await queue.fail(task_id, "something broke")
    result = await queue.poll_result(task_id, timeout=1.0)
    assert result.success is False
    assert result.has_signal(SignalType.SEMANTIC_ERROR)


async def test_prefix_isolation(queue) -> None:  # type: ignore[no-untyped-def]
    """Two queues with different prefixes do not interfere."""
    url, token = _require_credentials()
    from monet.core.queue_upstash import UpstashTaskQueue

    other_prefix = f"monet-test-other-{uuid.uuid4().hex[:8]}"
    other = UpstashTaskQueue(
        url=url, token=token, prefix=other_prefix, poll_interval=0.1
    )

    ctx = _make_ctx()
    await queue.enqueue("agent-a", "fast", ctx)

    # Other queue should see nothing.
    record = await other.claim("local")
    assert record is None

    # Original queue should see the task.
    record = await queue.claim("local")
    assert record is not None
    assert record["agent_id"] == "agent-a"

    # Cleanup other prefix keys.
    redis = Redis(url=url, token=token)
    cursor = "0"
    while True:
        cursor, keys = await redis.scan(cursor, match=f"{other_prefix}:*", count=100)
        if keys:
            for key in keys:
                await redis.delete(key)
        if cursor == "0" or cursor == 0:
            break


async def test_task_ttl_set(queue) -> None:  # type: ignore[no-untyped-def]
    """Task keys have a TTL set for auto-cleanup."""
    url, token = _require_credentials()

    ctx = _make_ctx()
    task_id = await queue.enqueue("agent", "fast", ctx)

    redis = Redis(url=url, token=token)
    key = f"{queue._prefix}:task:{task_id}"
    ttl = await redis.ttl(key)
    # TTL should be positive and within the configured range.
    assert ttl > 0
    assert ttl <= queue._task_ttl
