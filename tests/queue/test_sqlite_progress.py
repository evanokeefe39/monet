"""Tests for SqliteProgressBackend — ProgressWriter + ProgressReader protocols."""

from __future__ import annotations

import asyncio
import time

import pytest

from monet.queue._progress import EventType, ProgressEvent
from monet.queue.backends.sqlite_progress import SqliteProgressBackend


def _evt(
    event_type: EventType = EventType.STATUS,
    run_id: str = "run-1",
    task_id: str = "task-1",
    agent_id: str = "agent-1",
) -> ProgressEvent:
    return {
        "event_id": 0,
        "run_id": run_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "event_type": event_type,
        "timestamp_ms": int(time.time() * 1000),
    }


@pytest.fixture
def backend() -> SqliteProgressBackend:
    return SqliteProgressBackend(":memory:")


@pytest.mark.asyncio
async def test_record_returns_monotonic_ids(backend: SqliteProgressBackend) -> None:
    id1 = await backend.record("run-1", _evt())
    id2 = await backend.record("run-1", _evt())
    assert id1 > 0
    assert id2 > id1


@pytest.mark.asyncio
async def test_query_returns_events_after_cursor(
    backend: SqliteProgressBackend,
) -> None:
    id1 = await backend.record("run-1", _evt(EventType.AGENT_STARTED))
    id2 = await backend.record("run-1", _evt(EventType.AGENT_COMPLETED))
    await backend.record("run-2", _evt())  # different run, should not appear

    all_events = await backend.query("run-1")
    assert len(all_events) == 2

    events_after = await backend.query("run-1", after=id1)
    assert len(events_after) == 1
    assert events_after[0]["event_id"] == id2
    assert events_after[0]["event_type"] == EventType.AGENT_COMPLETED


@pytest.mark.asyncio
async def test_query_limit_respected(backend: SqliteProgressBackend) -> None:
    for _ in range(5):
        await backend.record("run-1", _evt())
    events = await backend.query("run-1", limit=3)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_record_preserves_optional_fields(backend: SqliteProgressBackend) -> None:
    event = _evt()
    event["trace_id"] = "trace-abc"
    event["payload"] = {"key": "value", "count": 42}
    await backend.record("run-1", event)

    events = await backend.query("run-1")
    assert len(events) == 1
    assert events[0]["trace_id"] == "trace-abc"
    assert events[0]["payload"] == {"key": "value", "count": 42}


@pytest.mark.asyncio
async def test_query_events_ordered_by_event_id(backend: SqliteProgressBackend) -> None:
    for et in [EventType.AGENT_STARTED, EventType.STATUS, EventType.AGENT_COMPLETED]:
        await backend.record("run-1", _evt(et))
    events = await backend.query("run-1")
    ids = [e["event_id"] for e in events]
    assert ids == sorted(ids)


@pytest.mark.asyncio
async def test_stream_terminates_on_run_completed(
    backend: SqliteProgressBackend,
) -> None:
    await backend.record("run-1", _evt(EventType.AGENT_STARTED))
    await backend.record("run-1", _evt(EventType.RUN_COMPLETED))

    collected: list[ProgressEvent] = []
    async for event in backend.stream("run-1"):
        collected.append(event)

    assert len(collected) == 2
    assert collected[-1]["event_type"] == EventType.RUN_COMPLETED


@pytest.mark.asyncio
async def test_stream_terminates_on_run_cancelled(
    backend: SqliteProgressBackend,
) -> None:
    await backend.record("run-1", _evt(EventType.RUN_CANCELLED))
    collected = [e async for e in backend.stream("run-1")]
    assert collected[-1]["event_type"] == EventType.RUN_CANCELLED


@pytest.mark.asyncio
async def test_stream_waits_for_new_events(backend: SqliteProgressBackend) -> None:
    collected: list[ProgressEvent] = []

    async def producer() -> None:
        await asyncio.sleep(0.05)
        await backend.record("run-1", _evt(EventType.AGENT_STARTED))
        await asyncio.sleep(0.05)
        await backend.record("run-1", _evt(EventType.RUN_COMPLETED))

    async def consumer() -> None:
        async for event in backend.stream("run-1"):
            collected.append(event)

    await asyncio.wait_for(
        asyncio.gather(producer(), consumer()),
        timeout=2.0,
    )
    assert len(collected) == 2


@pytest.mark.asyncio
async def test_has_cause_true(backend: SqliteProgressBackend) -> None:
    event = _evt(EventType.HITL_CAUSE)
    event["payload"] = {"cause_id": "cause-xyz"}
    await backend.record("run-1", event)
    assert await backend.has_cause("run-1", "cause-xyz") is True


@pytest.mark.asyncio
async def test_has_cause_false_wrong_run(backend: SqliteProgressBackend) -> None:
    event = _evt(EventType.HITL_CAUSE)
    event["payload"] = {"cause_id": "cause-xyz"}
    await backend.record("run-1", event)
    assert await backend.has_cause("run-2", "cause-xyz") is False


@pytest.mark.asyncio
async def test_has_cause_false_wrong_id(backend: SqliteProgressBackend) -> None:
    event = _evt(EventType.HITL_CAUSE)
    event["payload"] = {"cause_id": "cause-xyz"}
    await backend.record("run-1", event)
    assert await backend.has_cause("run-1", "cause-other") is False


@pytest.mark.asyncio
async def test_multiple_runs_isolated(backend: SqliteProgressBackend) -> None:
    await backend.record("run-A", _evt(run_id="run-A"))
    await backend.record("run-A", _evt(run_id="run-A"))
    await backend.record("run-B", _evt(run_id="run-B"))

    a_events = await backend.query("run-A")
    b_events = await backend.query("run-B")
    assert len(a_events) == 2
    assert len(b_events) == 1
