"""Task queue — public surface.

Backends are lazily imported via ``__getattr__`` so importing the
package doesn't pull optional dependencies. Interface types are eager.
"""

from __future__ import annotations

from typing import Any

from ._interface import TaskQueue, TaskRecord, TaskStatus

__all__ = [
    "InMemoryTaskQueue",
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
