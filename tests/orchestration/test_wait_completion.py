"""Race-scenario tests for wait_completion.

Verifies the subscribe-then-GET pattern in
``RedisStreamsTaskQueue.await_completion`` handles:

1. Result lands BEFORE subscribe (caught by initial GET).
2. Result lands AFTER subscribe (caught by PUBLISH notification).
3. Subscribe with no result and no publish times out cleanly.
4. Memory-backend dispatch works through the orchestration helper.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from monet.events import TaskRecord, TaskStatus
from monet.orchestration._invoke import wait_completion
from monet.queue import InMemoryTaskQueue
from monet.queue.backends.redis_streams import RedisStreamsTaskQueue
from monet.types import AgentResult


def _make_task(pool: str = "local") -> TaskRecord:
    return {
        "schema_version": 1,
        "task_id": str(uuid.uuid4()),
        "agent_id": "a",
        "command": "fast",
        "pool": pool,
        "context": {
            "task": "",
            "context": [],
            "command": "fast",
            "trace_id": "t",
            "run_id": "r",
            "agent_id": "a",
            "skills": [],
        },
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": datetime.now(UTC).isoformat(),
        "claimed_at": None,
        "completed_at": None,
    }


@pytest.fixture
async def streams_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[RedisStreamsTaskQueue]:
    fake = FakeAsyncRedis(decode_responses=True)
    q = RedisStreamsTaskQueue("redis://fake", lease_ttl_seconds=2)

    async def _ensure() -> FakeAsyncRedis:
        return fake

    monkeypatch.setattr(q, "_ensure_client", _ensure)
    yield q
    await fake.aclose()


async def test_result_arrives_before_subscribe(
    streams_queue: RedisStreamsTaskQueue,
) -> None:
    """Completion lands first; initial GET catches it without PUBSUB wait."""
    task = _make_task()
    task_id = await streams_queue.enqueue(task)
    await streams_queue.claim("local", consumer_id="c1", block_ms=200)
    result = AgentResult(success=True, output="early", trace_id="t", run_id="r")
    await streams_queue.complete(task_id, result)

    # wait_completion runs AFTER the result is already written.
    observed = await wait_completion(streams_queue, task_id, timeout=1.0)
    assert observed.output == "early"


async def test_result_arrives_after_subscribe(
    streams_queue: RedisStreamsTaskQueue,
) -> None:
    """wait_completion subscribes first, then completion fires."""
    task = _make_task()
    task_id = await streams_queue.enqueue(task)
    await streams_queue.claim("local", consumer_id="c1", block_ms=200)

    async def delayed_complete() -> None:
        await asyncio.sleep(0.1)
        result = AgentResult(success=True, output="late", trace_id="t", run_id="r")
        await streams_queue.complete(task_id, result)

    completer = asyncio.create_task(delayed_complete())
    try:
        observed = await wait_completion(streams_queue, task_id, timeout=2.0)
    finally:
        await completer
    assert observed.output == "late"


async def test_timeout_when_no_completion(
    streams_queue: RedisStreamsTaskQueue,
) -> None:
    task = _make_task()
    task_id = await streams_queue.enqueue(task)
    await streams_queue.claim("local", consumer_id="c1", block_ms=200)
    with pytest.raises(TimeoutError):
        await wait_completion(streams_queue, task_id, timeout=0.2)


async def test_wait_completion_memory_backend() -> None:
    """Memory path goes through the isinstance dispatch."""
    q = InMemoryTaskQueue()
    task = _make_task()
    task_id = await q.enqueue(task)
    await q.claim("local", consumer_id="c1", block_ms=200)
    result = AgentResult(success=True, output="m", trace_id="t", run_id="r")
    await q.complete(task_id, result)
    observed = await wait_completion(q, task_id, timeout=1.0)
    assert observed.output == "m"


async def test_wait_completion_rejects_non_protocol_backend() -> None:
    """A queue missing await_completion raises AttributeError."""

    class FakeQueue:
        pass

    with pytest.raises(AttributeError):
        await wait_completion(FakeQueue(), "t", timeout=0.1)  # type: ignore[arg-type]
