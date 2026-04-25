"""Tests for ProgressEvent types and protocol shapes."""

from __future__ import annotations

import time

from monet.queue._progress import (
    EventType,
    ProgressEvent,
    ProgressReader,
    ProgressWriter,
)


def _make_event(event_type: EventType) -> ProgressEvent:
    return {
        "event_id": 0,
        "run_id": "run-1",
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": event_type,
        "timestamp_ms": int(time.time() * 1000),
    }


def test_event_type_values() -> None:
    assert EventType.AGENT_STARTED == "agent_started"
    assert EventType.AGENT_COMPLETED == "agent_completed"
    assert EventType.AGENT_FAILED == "agent_failed"
    assert EventType.STREAM_UPDATE == "stream_update"
    assert EventType.HITL_CAUSE == "hitl_cause"
    assert EventType.HITL_DECISION == "hitl_decision"
    assert EventType.RUN_COMPLETED == "run_completed"
    assert EventType.RUN_CANCELLED == "run_cancelled"


def test_event_type_is_str() -> None:
    for member in EventType:
        assert isinstance(member, str)


def test_progress_event_required_fields() -> None:
    event = _make_event(EventType.AGENT_COMPLETED)
    assert event["run_id"] == "run-1"
    assert event["task_id"] == "task-1"
    assert event["agent_id"] == "agent-1"
    assert event["event_type"] == EventType.AGENT_COMPLETED
    assert event["event_id"] == 0
    assert isinstance(event["timestamp_ms"], int)


def test_progress_event_optional_fields() -> None:
    event = _make_event(EventType.STREAM_UPDATE)
    event["trace_id"] = "abc123"
    event["payload"] = {"message": "doing work"}
    assert event["trace_id"] == "abc123"
    assert event["payload"]["message"] == "doing work"


def test_event_type_round_trip() -> None:
    for member in EventType:
        assert EventType(member.value) == member


def test_progress_writer_protocol_shape() -> None:
    assert hasattr(ProgressWriter, "__protocol_attrs__") or hasattr(
        ProgressWriter, "_is_protocol"
    )
    assert "record" in dir(ProgressWriter)


def test_progress_reader_protocol_shape() -> None:
    assert "query" in dir(ProgressReader)
    assert "stream" in dir(ProgressReader)
    assert "has_cause" in dir(ProgressReader)


def test_public_exports() -> None:
    import monet.queue as q

    assert q.EventType is EventType
    assert q.ProgressEvent is ProgressEvent
    assert q.ProgressWriter is ProgressWriter
    assert q.ProgressReader is ProgressReader
