"""Event routing policy for the split-plane architecture.

classify_event maps a ProgressEvent to a routing policy that controls
whether the event lands in durable storage, the ephemeral UI stream,
or both. Adding a new EventType without a case here is a mypy error.
"""

from __future__ import annotations

from enum import StrEnum

from monet.contracts import EventType, ProgressEvent

__all__ = ["EventPolicy", "classify_event"]


class EventPolicy(StrEnum):
    """Routing policy for a ProgressEvent."""

    EPHEMERAL_UI = "ephemeral_ui"
    SILENT_AUDIT = "silent_audit"
    DUAL_ROUTED = "dual_routed"


def classify_event(event: ProgressEvent) -> EventPolicy:
    """Return the routing policy for *event*.

    Match is exhaustive over EventType — mypy flags unhandled variants
    when EventType grows.
    """
    match event["event_type"]:
        case EventType.STREAM_UPDATE:
            return EventPolicy.EPHEMERAL_UI
        case (
            EventType.AGENT_STARTED
            | EventType.AGENT_COMPLETED
            | EventType.AGENT_FAILED
            | EventType.RUN_COMPLETED
            | EventType.RUN_CANCELLED
            | EventType.HITL_CAUSE
            | EventType.HITL_DECISION
        ):
            return EventPolicy.DUAL_ROUTED
        case _:
            return EventPolicy.EPHEMERAL_UI
