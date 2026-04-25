from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from typing_extensions import TypedDict

if TYPE_CHECKING:
    from monet.types import AgentResult, AgentRunContext

__all__ = [
    "TASK_RECORD_SCHEMA_VERSION",
    "ClaimedTask",
    "TaskRecord",
    "TaskStatus",
]


class ClaimedTask(TypedDict):
    task_id: str
    run_id: str
    thread_id: str
    agent_id: str
    command: str
    pool: str


class TaskStatus(StrEnum):
    """Lifecycle states of a queued task."""

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


TASK_RECORD_SCHEMA_VERSION = 1


class TaskRecord(TypedDict):
    """Snapshot of a task at a point in time."""

    schema_version: int
    task_id: str
    agent_id: str
    command: str
    pool: str
    context: AgentRunContext
    status: TaskStatus
    result: AgentResult | None
    created_at: str
    claimed_at: str | None
    completed_at: str | None
