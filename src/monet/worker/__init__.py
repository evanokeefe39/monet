"""Worker package — claim loop and dispatch backends."""

from __future__ import annotations

from typing import Any

__all__ = ["run_worker"]


def __getattr__(name: str) -> Any:
    if name == "run_worker":
        from monet.worker._loop import run_worker

        return run_worker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
