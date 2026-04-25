"""Protocol conformance tests for PostgresProgressBackend.

Skipped unless MONET_PROGRESS_DSN is set to a reachable Postgres DSN.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from monet.contracts import EventType, ProgressEvent

_DSN = os.environ.get("MONET_PROGRESS_DSN")
pytestmark = pytest.mark.skipif(not _DSN, reason="MONET_PROGRESS_DSN not set")


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
async def backend():  # type: ignore[return]
    from monet.queue.backends.postgres_progress import PostgresProgressBackend

    b = PostgresProgressBackend(_DSN)  # type: ignore[arg-type]
    await b.open()
    yield b
    await b.close()


# ---------------------------------------------------------------------------
# record() assigns monotonic event_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_returns_positive_event_id(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    eid = await backend.record(rid, _event(rid))
    assert eid > 0


@pytest.mark.asyncio
async def test_record_monotonic_within_run(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    e1 = await backend.record(rid, _event(rid))
    e2 = await backend.record(rid, _event(rid))
    assert e2 > e1


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_returns_events_in_order(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    for _ in range(3):
        await backend.record(rid, _event(rid))
    events = await backend.query(rid)
    ids = [e["event_id"] for e in events]
    assert ids == sorted(ids)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_query_after_cursor(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    e1 = await backend.record(rid, _event(rid))
    await backend.record(rid, _event(rid))
    await backend.record(rid, _event(rid))
    events = await backend.query(rid, after=e1)
    assert all(e["event_id"] > e1 for e in events)
    assert len(events) == 2


@pytest.mark.asyncio
async def test_query_limit(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    for _ in range(5):
        await backend.record(rid, _event(rid))
    events = await backend.query(rid, limit=3)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_query_preserves_payload(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    cause_id = str(uuid.uuid4())
    e = _event(rid, EventType.HITL_CAUSE, cause_id=cause_id)
    await backend.record(rid, e)
    events = await backend.query(rid)
    assert events[0]["payload"] == {"cause_id": cause_id}


# ---------------------------------------------------------------------------
# has_cause() / has_decision()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_has_cause_false_before_record(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    assert not await backend.has_cause(rid, "no-cause")


@pytest.mark.asyncio
async def test_has_cause_true_after_record(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    cause_id = str(uuid.uuid4())
    await backend.record(rid, _event(rid, EventType.HITL_CAUSE, cause_id=cause_id))
    assert await backend.has_cause(rid, cause_id)
    assert not await backend.has_cause(rid, "other")


@pytest.mark.asyncio
async def test_has_decision_false_before_record(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    assert not await backend.has_decision(rid, "no-cause")


@pytest.mark.asyncio
async def test_has_decision_true_after_record(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    cause_id = str(uuid.uuid4())
    e = _event(rid, EventType.HITL_DECISION, cause_id=cause_id)
    await backend.record(rid, e)
    assert await backend.has_decision(rid, cause_id)
    assert not await backend.has_decision(rid, "other")


# ---------------------------------------------------------------------------
# stream() — terminates on terminal event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_terminates_on_run_completed(backend) -> None:  # type: ignore[no-untyped-def]
    rid = f"run-{uuid.uuid4()}"
    await backend.record(rid, _event(rid, EventType.AGENT_STARTED))
    await backend.record(rid, _event(rid, EventType.RUN_COMPLETED))
    events = []
    async for ev in backend.stream(rid):
        events.append(ev)
    assert len(events) == 2
    assert str(events[-1]["event_type"]) == EventType.RUN_COMPLETED
