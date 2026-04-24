"""Progress event types and writer/reader protocols.

Phase 0 foundation for the split-plane architecture. Workers and the
@agent decorator write ProgressEvent records; the data-plane app and
SSE endpoint read them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = [
    "EventType",
    "ProgressEvent",
    "ProgressReader",
    "ProgressWriter",
]


class EventType(StrEnum):
    """Known progress event types."""

    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    STATUS = "status"
    HITL_CAUSE = "hitl_cause"
    HITL_DECISION = "hitl_decision"
    RUN_COMPLETED = "run_completed"
    RUN_CANCELLED = "run_cancelled"


class ProgressEvent(TypedDict, total=False):
    """Typed, attributed record of what happened during a task.

    Required fields are always present. Optional enrichment fields use
    ``total=False`` so construction errors surface at write time via
    TypedDict narrowing.
    """

    # Required — never absent
    event_id: int  # 0 before write; assigned by ProgressWriter.record()
    run_id: str
    task_id: str
    agent_id: str
    event_type: EventType
    timestamp_ms: int
    # Optional enrichment
    trace_id: str
    payload: dict[str, Any]


# Required fields kept in a separate total=True class and merged via inheritance
# isn't supported cleanly in TypedDict; use runtime validation in ProgressWriter
# implementations. Construction-time errors are caught by mypy's TypedDict checks.


class ProgressWriter(Protocol):
    """Write interface — workers and the decorator record events here."""

    async def record(self, run_id: str, event: ProgressEvent) -> int:
        """Append event. Returns assigned event_id. Monotonic within run_id."""
        ...


class ProgressReader(Protocol):
    """Read/stream interface — SSE endpoint and audit queries read here."""

    async def query(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[ProgressEvent]:
        """Return stored events for run_id with event_id > after."""
        ...

    def stream(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[ProgressEvent]:
        """Yield events as they arrive. Terminates on RUN_COMPLETED/RUN_CANCELLED."""
        ...

    async def has_cause(self, run_id: str, cause_id: str) -> bool:
        """Return True if a HITL_CAUSE event with payload.cause_id exists."""
        ...

    async def has_decision(self, run_id: str, cause_id: str) -> bool:
        """Return True if a HITL_DECISION event with payload.cause_id exists."""
        ...
