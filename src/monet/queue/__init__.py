"""Task queue — public surface.

Backends are lazily imported via ``__getattr__`` so importing the
package doesn't pull optional dependencies. Interface types are eager.
"""

from __future__ import annotations

from typing import Any

from ._interface import (
    TASK_RECORD_SCHEMA_VERSION,
    AwaitAlreadyConsumedError,
    ProgressStore,
    QueueMaintenance,
    TaskQueue,
    TaskRecord,
    TaskStatus,
)
from ._progress import (
    EventType,
    ProgressEvent,
    ProgressReader,
    ProgressWriter,
)

__all__ = [
    "TASK_RECORD_SCHEMA_VERSION",
    "AwaitAlreadyConsumedError",
    "EventType",
    "InMemoryTaskQueue",
    "ProgressEvent",
    "ProgressReader",
    "ProgressStore",
    "ProgressWriter",
    "QueueMaintenance",
    "TaskQueue",
    "TaskRecord",
    "TaskStatus",
    "run_worker",
]


def __getattr__(name: str) -> Any:
    if name == "InMemoryTaskQueue":
        from .backends.memory import InMemoryTaskQueue

        return InMemoryTaskQueue
    if name == "run_worker":
        from ._worker import run_worker

        return run_worker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
