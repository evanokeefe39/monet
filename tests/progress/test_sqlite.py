"""Protocol conformance tests for SqliteProgressBackend."""

from __future__ import annotations

import time
import uuid

import pytest

from monet.contracts import EventType, ProgressEvent
from monet.progress.backends.sqlite import SqliteProgressBackend


def _event(
    run_id: str,
    event_type: EventType = EventType.AGENT_STARTED,
    *,
    cause_id: str | None = None,
) -> ProgressEvent:
    payload: dict = {}
    if cause_id is not None:
        payload["cause_id"] = cause_id
    e: ProgressEvent = {
        "event_id": 0,
        "run_id": run_id,
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": event_type,
        "timestamp_ms": int(time.time() * 1000),
    }
    if payload:
        e["payload"] = payload
    return e


@pytest.fixture
def backend() -> SqliteProgressBackend:
    return SqliteProgressBackend(":memory:")


# ---------------------------------------------------------------------------
# record() assigns monotonic event_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_returns_positive_event_id(backend: SqliteProgressBackend) -> None:
    eid = await backend.record("run-1", _event("run-1"))
    assert eid > 0


@pytest.mark.asyncio
async def test_record_monotonic_within_run(backend: SqliteProgressBackend) -> None:
    e1 = await backend.record("run-1", _event("run-1"))
    e2 = await backend.record("run-1", _event("run-1"))
    assert e2 > e1


@pytest.mark.asyncio
async def test_record_independent_across_runs(backend: SqliteProgressBackend) -> None:
    e1 = await backend.record("run-a", _event("run-a"))
    e2 = await backend.record("run-b", _event("run-b"))
    assert e1 > 0 and e2 > 0


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_returns_events_in_order(backend: SqliteProgressBackend) -> None:
    for _ in range(3):
        await backend.record("run-q", _event("run-q"))
    events = await backend.query("run-q")
    ids = [e["event_id"] for e in events]
    assert ids == sorted(ids)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_query_after_cursor(backend: SqliteProgressBackend) -> None:
    e1 = await backend.record("run-c", _event("run-c"))
    await backend.record("run-c", _event("run-c"))
    await backend.record("run-c", _event("run-c"))
    events = await backend.query("run-c", after=e1)
    assert all(e["event_id"] > e1 for e in events)
    assert len(events) == 2


@pytest.mark.asyncio
async def test_query_limit(backend: SqliteProgressBackend) -> None:
    for _ in range(5):
        await backend.record("run-l", _event("run-l"))
    events = await backend.query("run-l", limit=3)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_query_empty_run(backend: SqliteProgressBackend) -> None:
    events = await backend.query("run-missing")
    assert events == []


@pytest.mark.asyncio
async def test_query_preserves_payload(backend: SqliteProgressBackend) -> None:
    cause_id = str(uuid.uuid4())
    e = _event("run-p", EventType.HITL_CAUSE, cause_id=cause_id)
    await backend.record("run-p", e)
    events = await backend.query("run-p")
    assert events[0]["payload"] == {"cause_id": cause_id}


@pytest.mark.asyncio
async def test_query_preserves_trace_id(backend: SqliteProgressBackend) -> None:
    e = _event("run-tr")
    e["trace_id"] = "trace-abc"
    await backend.record("run-tr", e)
    events = await backend.query("run-tr")
    assert events[0]["trace_id"] == "trace-abc"


# ---------------------------------------------------------------------------
# stream() — terminates on terminal event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_terminates_on_run_completed(
    backend: SqliteProgressBackend,
) -> None:
    await backend.record("run-s", _event("run-s", EventType.AGENT_STARTED))
    await backend.record("run-s", _event("run-s", EventType.RUN_COMPLETED))
    events = []
    async for ev in backend.stream("run-s"):
        events.append(ev)
    assert len(events) == 2
    assert str(events[-1]["event_type"]) == EventType.RUN_COMPLETED


@pytest.mark.asyncio
async def test_stream_terminates_on_run_cancelled(
    backend: SqliteProgressBackend,
) -> None:
    await backend.record("run-sc", _event("run-sc", EventType.RUN_CANCELLED))
    events = []
    async for ev in backend.stream("run-sc"):
        events.append(ev)
    assert len(events) == 1


# ---------------------------------------------------------------------------
# has_cause() / has_decision()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_has_cause_false_before_record(backend: SqliteProgressBackend) -> None:
    assert not await backend.has_cause("run-x", "no-cause")


@pytest.mark.asyncio
async def test_has_cause_true_after_record(backend: SqliteProgressBackend) -> None:
    cause_id = str(uuid.uuid4())
    await backend.record(
        "run-hc", _event("run-hc", EventType.HITL_CAUSE, cause_id=cause_id)
    )
    assert await backend.has_cause("run-hc", cause_id)
    assert not await backend.has_cause("run-hc", "other")


@pytest.mark.asyncio
async def test_has_decision_false_before_record(backend: SqliteProgressBackend) -> None:
    assert not await backend.has_decision("run-d", "no-cause")


@pytest.mark.asyncio
async def test_has_decision_true_after_record(backend: SqliteProgressBackend) -> None:
    cause_id = str(uuid.uuid4())
    e = _event("run-hd", EventType.HITL_DECISION, cause_id=cause_id)
    await backend.record("run-hd", e)
    assert await backend.has_decision("run-hd", cause_id)
    assert not await backend.has_decision("run-hd", "other")
