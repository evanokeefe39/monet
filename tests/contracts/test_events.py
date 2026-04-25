"""Tests for contracts._events package."""

from __future__ import annotations

import time

from monet.contracts import EventType, ProgressEvent
from monet.server._event_router import EventPolicy, classify_event


def _event(event_type: EventType) -> ProgressEvent:
    return {
        "event_id": 0,
        "run_id": "run-1",
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": event_type,
        "timestamp_ms": int(time.time() * 1000),
    }


def test_progress_event_construction() -> None:
    e = _event(EventType.AGENT_STARTED)
    assert e["event_type"] == EventType.AGENT_STARTED
    assert e["event_id"] == 0


def test_event_type_values() -> None:
    assert EventType.STREAM_UPDATE == "stream_update"
    assert EventType.AGENT_STARTED == "agent_started"
    assert EventType.AGENT_COMPLETED == "agent_completed"
    assert EventType.AGENT_FAILED == "agent_failed"
    assert EventType.HITL_CAUSE == "hitl_cause"
    assert EventType.HITL_DECISION == "hitl_decision"
    assert EventType.RUN_COMPLETED == "run_completed"
    assert EventType.RUN_CANCELLED == "run_cancelled"


def test_stream_update_is_ephemeral() -> None:
    assert classify_event(_event(EventType.STREAM_UPDATE)) == EventPolicy.EPHEMERAL_UI


def test_all_domain_events_are_dual_routed() -> None:
    domain = [
        EventType.AGENT_STARTED,
        EventType.AGENT_COMPLETED,
        EventType.AGENT_FAILED,
        EventType.HITL_CAUSE,
        EventType.HITL_DECISION,
        EventType.RUN_COMPLETED,
        EventType.RUN_CANCELLED,
    ]
    for et in domain:
        assert classify_event(_event(et)) == EventPolicy.DUAL_ROUTED, (
            f"{et} not DUAL_ROUTED"
        )


def test_progress_event_optional_fields() -> None:
    e: ProgressEvent = {
        "event_id": 5,
        "run_id": "r",
        "task_id": "t",
        "agent_id": "a",
        "event_type": EventType.AGENT_COMPLETED,
        "timestamp_ms": 1000,
        "trace_id": "abc",
        "payload": {"key": "val"},
    }
    assert e["trace_id"] == "abc"
    assert e["payload"] == {"key": "val"}
