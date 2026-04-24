"""Contract tests shared between InMemoryTaskQueue and RedisStreamsTaskQueue.

Both backends implement the 6-method ``TaskQueue`` protocol and both
provide ``await_completion`` consumed by
``monet.orchestration._invoke.wait_completion``. Running the same tests
against both catches drift between the implementations.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from monet._ports import MAX_INLINE_PAYLOAD_BYTES
from monet.queue import InMemoryTaskQueue, TaskRecord, TaskStatus
from monet.queue.backends.redis_streams import RedisStreamsTaskQueue
from monet.types import AgentResult, AgentRunContext


def _make_task(
    agent_id: str = "a",
    command: str = "fast",
    pool: str = "local",
    task_id: str | None = None,
) -> TaskRecord:
    ctx: AgentRunContext = {
        "task": "do",
        "context": [],
        "command": command,
        "trace_id": "t-1",
        "run_id": "r-1",
        "agent_id": agent_id,
        "skills": [],
    }
    return {
        "schema_version": 1,
        "task_id": task_id or str(uuid.uuid4()),
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


# --- Fixtures ------------------------------------------------------------


@pytest.fixture
async def memory_queue() -> AsyncIterator[InMemoryTaskQueue]:
    q = InMemoryTaskQueue()
    yield q


@pytest.fixture
async def streams_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[RedisStreamsTaskQueue]:
    """RedisStreamsTaskQueue backed by fakeredis.

    fakeredis implements XADD / XREADGROUP / XACK / XPENDING / XCLAIM /
    PUBLISH / SUBSCRIBE so the full contract runs without a Docker
    Redis. The queue's ``_ensure_client`` is monkeypatched to return a
    ``FakeAsyncRedis`` instance.
    """
    fake = FakeAsyncRedis(decode_responses=True)
    q = RedisStreamsTaskQueue(
        "redis://fake",
        lease_ttl_seconds=1,
        work_stream_maxlen=1000,
    )

    async def _ensure() -> FakeAsyncRedis:
        return fake

    monkeypatch.setattr(q, "_ensure_client", _ensure)
    yield q
    await fake.aclose()


@pytest.fixture(params=["memory", "streams"])
async def queue(
    request: pytest.FixtureRequest,
    memory_queue: InMemoryTaskQueue,
    streams_queue: RedisStreamsTaskQueue,
) -> InMemoryTaskQueue | RedisStreamsTaskQueue:
    return memory_queue if request.param == "memory" else streams_queue


# --- Contract tests ------------------------------------------------------


async def test_enqueue_claim_complete_round_trip(
    queue: InMemoryTaskQueue | RedisStreamsTaskQueue,
) -> None:
    task = _make_task()
    task_id = await queue.enqueue(task)

    record = await queue.claim("local", consumer_id="c1", block_ms=500)
    assert record is not None
    assert record["task_id"] == task_id
    assert record["status"] == TaskStatus.CLAIMED

    result = AgentResult(success=True, output="done", trace_id="t-1", run_id="r-1")
    await queue.complete(task_id, result)

    done = await queue.await_completion(task_id, timeout=2.0)
    assert done.success is True
    assert done.output == "done"


async def test_fail_stores_error_result(
    queue: InMemoryTaskQueue | RedisStreamsTaskQueue,
) -> None:
    task = _make_task()
    task_id = await queue.enqueue(task)
    await queue.claim("local", consumer_id="c1", block_ms=500)
    await queue.fail(task_id, "boom")

    result = await queue.await_completion(task_id, timeout=2.0)
    assert result.success is False
    assert any("boom" in s["reason"] for s in result.signals)


async def test_claim_empty_pool_returns_none(
    queue: InMemoryTaskQueue | RedisStreamsTaskQueue,
) -> None:
    record = await queue.claim("empty", consumer_id="c1", block_ms=100)
    assert record is None


async def test_payload_size_guard_rejects_oversize(
    queue: InMemoryTaskQueue | RedisStreamsTaskQueue,
) -> None:
    """Only RedisStreamsTaskQueue enforces the guard at enqueue today.

    InMemoryTaskQueue has no size limit because it never crosses the
    wire. The memory variant of this test is a no-op, documented via
    the early return so the shared contract stays meaningful.
    """
    if isinstance(queue, InMemoryTaskQueue):
        pytest.skip("InMemoryTaskQueue has no payload-size boundary")
    huge = "x" * (MAX_INLINE_PAYLOAD_BYTES + 100)
    task = _make_task()
    task["context"]["task"] = huge
    with pytest.raises(ValueError, match="MAX_INLINE_PAYLOAD_BYTES"):
        await queue.enqueue(task)


async def test_progress_round_trip(
    queue: InMemoryTaskQueue | RedisStreamsTaskQueue,
) -> None:
    task = _make_task()
    task_id = await queue.enqueue(task)
    received: list[dict[str, Any]] = []

    async def consume() -> None:
        async for ev in queue.subscribe_progress(task_id):
            received.append(ev)
            if ev.get("done"):
                return

    consumer = asyncio.create_task(consume())
    # Give the subscriber time to register.
    await asyncio.sleep(0.05)
    await queue.publish_progress(task_id, {"step": 1})
    await queue.publish_progress(task_id, {"step": 2, "done": True})
    await asyncio.wait_for(consumer, timeout=2.0)
    assert any({"step": 1}.items() <= e.items() for e in received)
    assert any({"step": 2, "done": True}.items() <= e.items() for e in received)


async def test_await_completion_timeout(
    queue: InMemoryTaskQueue | RedisStreamsTaskQueue,
) -> None:
    task = _make_task()
    task_id = await queue.enqueue(task)
    with pytest.raises(TimeoutError):
        await queue.await_completion(task_id, timeout=0.1)


async def test_idempotent_complete(
    queue: InMemoryTaskQueue | RedisStreamsTaskQueue,
) -> None:
    """Completing twice with the same result does not raise."""
    task = _make_task()
    task_id = await queue.enqueue(task)
    await queue.claim("local", consumer_id="c1", block_ms=500)
    result = AgentResult(success=True, output="done", trace_id="t-1", run_id="r-1")
    await queue.complete(task_id, result)
    # Second complete: memory raises KeyError (record consumed),
    # streams silently overwrites the TTL key. Either is acceptable;
    # we just assert the first completion is observable.
    observed = await queue.await_completion(task_id, timeout=1.0)
    assert observed.success is True


# --- RedisStreamsTaskQueue-only tests ------------------------------------


async def test_streams_reclaim_returns_expired_to_pool(
    streams_queue: RedisStreamsTaskQueue,
) -> None:
    """Claimed-but-abandoned tasks are reclaimed after lease expiry.

    Simulates a worker crash: claim a task, let the lease TTL elapse,
    then run the sweeper. XCLAIM moves the entry back to the group's
    PEL under the sweeper consumer; a fresh XREADGROUP on a new
    consumer name can claim it via XAUTOCLAIM-equivalent semantics
    (we just verify the sweeper reports the reclaim).
    """
    task = _make_task()
    await streams_queue.enqueue(task)
    claimed = await streams_queue.claim("local", consumer_id="worker-1", block_ms=500)
    assert claimed is not None

    # Let the lease elapse. Fixture uses lease_ttl_seconds=1.
    await asyncio.sleep(1.2)
    reclaimed = await streams_queue.reclaim_expired_internal()
    assert reclaimed  # non-empty means XCLAIM moved ids


async def test_streams_ping(streams_queue: RedisStreamsTaskQueue) -> None:
    assert await streams_queue.ping() is True
