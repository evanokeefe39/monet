"""Server-side entry point for the quickstart example.

Configures monet infrastructure and exports the three graph builders
for ``langgraph dev`` to serve. The ``langgraph.json`` in this directory
points here.

Start with::

    cd examples/quickstart
    uv run langgraph dev
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import monet.agents  # noqa: F401 — side-effect: registers reference agents
from monet.catalogue import catalogue_from_env, configure_catalogue
from monet.orchestration import (
    build_entry_graph,
    build_execution_graph,
    build_planning_graph,
    configure_queue,
)
from monet.queue import InMemoryTaskQueue, run_worker
from monet.tracing import configure_tracing

# ── Infrastructure init (runs at import time) ───────────────────────
configure_tracing()
configure_catalogue(
    catalogue_from_env(default_root=Path(__file__).resolve().parent / ".catalogue")
)

# ── Queue + lazy worker ─────────────────────────────────────────────
# The worker starts on first enqueue because langgraph dev provides
# the event loop at graph invocation time, not at import time.
_queue = InMemoryTaskQueue()
configure_queue(_queue)

_worker_task: asyncio.Task[None] | None = None
_orig_enqueue = _queue.enqueue


async def _lazy_enqueue(
    agent_id: str, command: str, ctx: Any, pool: str = "local"
) -> str:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(run_worker(_queue))
    return await _orig_enqueue(agent_id, command, ctx, pool=pool)


_queue.enqueue = _lazy_enqueue  # type: ignore[assignment]

__all__ = ["build_entry_graph", "build_execution_graph", "build_planning_graph"]
