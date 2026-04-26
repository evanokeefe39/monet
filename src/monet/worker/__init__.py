"""Worker package — claim loop, dispatch backends, and remote queue client."""

from __future__ import annotations

from typing import Any

__all__ = ["RemoteQueue", "WorkerClient", "run_worker"]


def __getattr__(name: str) -> Any:
    if name == "run_worker":
        from monet.worker._loop import run_worker

        return run_worker
    if name in ("WorkerClient", "RemoteQueue"):
        from monet.worker._client import RemoteQueue, WorkerClient

        return {"WorkerClient": WorkerClient, "RemoteQueue": RemoteQueue}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
