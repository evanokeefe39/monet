"""Task queue — public surface.

Backends are lazily imported via ``__getattr__`` so importing the
package doesn't pull optional dependencies (aiosqlite, redis,
upstash-redis). Interface types are eager.
"""

from __future__ import annotations

from typing import Any

from ._interface import TaskQueue, TaskRecord, TaskStatus

__all__ = [
    "InMemoryTaskQueue",
    "RedisTaskQueue",
    "SQLiteTaskQueue",
    "TaskQueue",
    "TaskRecord",
    "TaskStatus",
    "UpstashTaskQueue",
    "run_worker",
]


def __getattr__(name: str) -> Any:
    if name == "InMemoryTaskQueue":
        from .backends.memory import InMemoryTaskQueue

        return InMemoryTaskQueue
    if name == "SQLiteTaskQueue":
        from .backends.sqlite import SQLiteTaskQueue

        return SQLiteTaskQueue
    if name == "RedisTaskQueue":
        from .backends.redis import RedisTaskQueue

        return RedisTaskQueue
    if name == "UpstashTaskQueue":
        from .backends.upstash import UpstashTaskQueue

        return UpstashTaskQueue
    if name == "run_worker":
        from ._worker import run_worker

        return run_worker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
