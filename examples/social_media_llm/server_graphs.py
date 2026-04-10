"""Server-side graph entry point for ``langgraph dev``.

The reference agents in :mod:`monet.agents` register themselves on
import as a side effect of the ``@agent`` decorator, populating both
the handler registry (worker-side) and the capability manifest
(orchestration-side).

This module imports ``monet.agents`` for side effects, configures
tracing, catalogue, and queue, then re-exports the three graph builders.
The example's ``langgraph.json`` points at this module.

The worker starts lazily on the first ``enqueue()`` call because the
LangGraph dev server provides the async event loop at graph invocation
time — not at import time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import monet.agents  # noqa: F401 — registers reference agents
from monet.catalogue import catalogue_from_env, configure_catalogue
from monet.orchestration import (
    build_entry_graph,
    build_execution_graph,
    build_planning_graph,
    configure_queue,
)
from monet.queue import InMemoryTaskQueue, run_worker
from monet.tracing import configure_tracing

# ── Sync init (runs at import time) ─────────────────────────────────
configure_tracing()

_default_root = Path(__file__).resolve().parent / ".catalogue"
configure_catalogue(catalogue_from_env(default_root=_default_root))

# ── Queue + lazy worker ─────────────────────────────────────────────
_queue = InMemoryTaskQueue()
configure_queue(_queue)

_worker_task: asyncio.Task[None] | None = None
_orig_enqueue = _queue.enqueue


async def _lazy_enqueue(
    agent_id: str,
    command: str,
    ctx: Any,
    pool: str = "local",
) -> str:
    """Start the worker on first enqueue, then delegate."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(run_worker(_queue))
    return await _orig_enqueue(agent_id, command, ctx, pool=pool)


_queue.enqueue = _lazy_enqueue  # type: ignore[assignment]


__all__ = ["build_entry_graph", "build_execution_graph", "build_planning_graph"]
