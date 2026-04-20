"""Push dispatch restart recovery.

Verifies that in-flight push dispatch records written to Redis are picked
up and reissued by ``_reissue_in_flight_push`` at server startup.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis

from monet.queue.backends.redis_streams import RedisStreamsTaskQueue
from monet.server._aegra_routes import _reissue_in_flight_push


def _make_streams_queue(fake: FakeAsyncRedis) -> RedisStreamsTaskQueue:
    q = RedisStreamsTaskQueue("redis://fake", lease_ttl_seconds=60)

    async def _ensure() -> FakeAsyncRedis:
        return fake

    q._ensure_client = _ensure  # type: ignore[method-assign]
    return q


@pytest.fixture
async def fake_redis() -> FakeAsyncRedis:
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


async def test_reissue_dispatches_in_flight_task(
    fake_redis: FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-flight push dispatch record is reissued as a background task."""
    q = _make_streams_queue(fake_redis)
    task_id = "task-abc"
    task_payload = json.dumps({"task_id": task_id, "agent_id": "a", "command": "fast"})

    await q.record_push_dispatch(
        task_id,
        "http://worker.example/dispatch",
        "secret",
        task_payload,
        attempt=0,
    )

    dispatched: list[str] = []

    async def _fake_push_with_retry(*args: Any, **kwargs: Any) -> None:
        dispatched.append(args[0])  # task_id

    monkeypatch.setattr("monet.orchestration.push_with_retry", _fake_push_with_retry)
    monkeypatch.setenv("MONET_API_KEY", "test-api-key")
    monkeypatch.setenv("MONET_SERVER_URL", "http://orchestrator.example")

    await _reissue_in_flight_push(q)

    # Yield the event loop so the create_task coroutine can run.
    import asyncio

    await asyncio.sleep(0.05)
    assert task_id in dispatched


async def test_reissue_fails_exhausted_task(
    fake_redis: FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task that already exhausted retries before restart is failed, not reissued."""
    q = _make_streams_queue(fake_redis)
    task_id = "task-exhausted"
    task_payload = json.dumps({"task_id": task_id, "agent_id": "a", "command": "fast"})

    await q.record_push_dispatch(
        task_id,
        "http://worker.example/dispatch",
        None,
        task_payload,
        attempt=3,  # _PUSH_MAX_ATTEMPTS
    )

    write_failed_calls: list[str] = []

    async def _fake_write_failed(tid: str, *_: Any, **__: Any) -> None:
        write_failed_calls.append(tid)

    monkeypatch.setattr("monet.orchestration.write_dispatch_failed", _fake_write_failed)
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    monkeypatch.setenv("MONET_SERVER_URL", "http://localhost:2026")

    await _reissue_in_flight_push(q)
    assert task_id in write_failed_calls


async def test_reissue_skips_non_redis_queue() -> None:
    """_reissue_in_flight_push is a no-op for non-Redis queues."""
    from monet.queue import InMemoryTaskQueue

    q = InMemoryTaskQueue()
    # Should not raise
    await _reissue_in_flight_push(q)


async def test_record_and_pop_push_dispatch(
    fake_redis: FakeAsyncRedis,
) -> None:
    """record_push_dispatch persists a record; pop_push_dispatch removes it."""
    q = _make_streams_queue(fake_redis)
    task_id = "task-xyz"

    await q.record_push_dispatch(
        task_id, "http://x.example", "s", '{"task_id":"x"}', attempt=1
    )
    records = await q.list_in_flight_push_dispatches()
    assert any(r["task_id"] == task_id for r in records)
    assert any(r["attempt"] == "1" for r in records if r["task_id"] == task_id)

    await q.pop_push_dispatch(task_id)
    records_after = await q.list_in_flight_push_dispatches()
    assert not any(r["task_id"] == task_id for r in records_after)
