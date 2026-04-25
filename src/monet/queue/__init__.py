"""Task queue — public surface.

Backends are lazily imported via ``__getattr__`` so importing the
package doesn't pull optional dependencies. Interface types are eager.
"""

from __future__ import annotations

from typing import Any

from ._interface import (
    AwaitAlreadyConsumedError,
    ProgressStore,
    QueueMaintenance,
    TaskQueue,
)

__all__ = [
    "AwaitAlreadyConsumedError",
    "InMemoryTaskQueue",
    "ProgressStore",
    "QueueMaintenance",
    "TaskQueue",
]


def __getattr__(name: str) -> Any:
    if name == "InMemoryTaskQueue":
        from .backends.memory import InMemoryTaskQueue

        return InMemoryTaskQueue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
