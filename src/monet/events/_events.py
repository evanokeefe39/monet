from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

__all__ = ["EventType", "ProgressEvent"]


class EventType(StrEnum):
    # Domain events — durable, audit-grade
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    HITL_CAUSE = "hitl_cause"
    HITL_DECISION = "hitl_decision"
    RUN_COMPLETED = "run_completed"
    RUN_CANCELLED = "run_cancelled"
    # Stream events — ephemeral, UI-only, not stored
    STREAM_UPDATE = "stream_update"


class ProgressEvent(TypedDict, total=False):
    # Required fields
    event_id: int
    run_id: str
    task_id: str
    agent_id: str
    event_type: EventType
    timestamp_ms: int
    # Optional enrichment
    trace_id: str
    payload: dict[str, Any]
