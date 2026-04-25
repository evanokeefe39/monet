"""Tests for classify_event exhaustiveness and policy correctness."""

from __future__ import annotations

import time

import pytest

from monet.events import EventType, ProgressEvent
from monet.server._event_router import EventPolicy, classify_event


def _event(event_type: EventType) -> ProgressEvent:
    return {
        "event_id": 0,
        "run_id": "r",
        "task_id": "t",
        "agent_id": "a",
        "event_type": event_type,
        "timestamp_ms": int(time.time() * 1000),
    }


@pytest.mark.parametrize(
    "event_type,expected",
    [
        (EventType.STREAM_UPDATE, EventPolicy.EPHEMERAL_UI),
        (EventType.AGENT_STARTED, EventPolicy.DUAL_ROUTED),
        (EventType.AGENT_COMPLETED, EventPolicy.DUAL_ROUTED),
        (EventType.AGENT_FAILED, EventPolicy.DUAL_ROUTED),
        (EventType.RUN_COMPLETED, EventPolicy.DUAL_ROUTED),
        (EventType.RUN_CANCELLED, EventPolicy.DUAL_ROUTED),
        (EventType.HITL_CAUSE, EventPolicy.DUAL_ROUTED),
        (EventType.HITL_DECISION, EventPolicy.DUAL_ROUTED),
    ],
)
def test_classify_event(event_type: EventType, expected: EventPolicy) -> None:
    assert classify_event(_event(event_type)) == expected


def test_all_event_types_classified() -> None:
    """Every EventType member has an explicit policy — no fall-through gaps."""
    for member in EventType:
        policy = classify_event(_event(member))
        assert isinstance(policy, EventPolicy), f"{member} returned non-Policy"


def test_event_policy_values() -> None:
    assert EventPolicy.EPHEMERAL_UI == "ephemeral_ui"
    assert EventPolicy.SILENT_AUDIT == "silent_audit"
    assert EventPolicy.DUAL_ROUTED == "dual_routed"
