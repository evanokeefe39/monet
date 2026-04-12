"""Default graph exports for Aegra / LangGraph dev servers.

Point ``aegra.json`` (or ``langgraph.json``) here to serve the three
monet graphs with zero configuration.

Infrastructure (tracing, catalogue, queue, worker) is configured at
import time using environment defaults.  Override via env vars:

- ``MONET_CATALOGUE_DIR`` — catalogue storage path (default: ``.catalogue``)
- ``MONET_QUEUE_BACKEND`` — queue backend: ``memory`` (default),
  ``redis``, or ``sqlite``
- ``OTEL_EXPORTER_OTLP_ENDPOINT`` / ``LANGFUSE_*`` — tracing backend
"""

from __future__ import annotations

import os

import monet.agents  # noqa: F401 — registers reference agents
from monet.catalogue import catalogue_from_env, configure_catalogue
from monet.core.tracing import configure_tracing
from monet.orchestration import (
    build_entry_graph,
    build_execution_graph,
    build_planning_graph,
    configure_queue,
)
from monet.queue import InMemoryTaskQueue, TaskQueue
from monet.server import configure_lazy_worker


def _create_queue() -> TaskQueue:
    """Create a task queue from the ``MONET_QUEUE_BACKEND`` env var.

    Supported values:

    - ``memory`` (default): in-process queue, suitable for sidecar workers.
    - ``redis``: Redis-backed queue (requires ``REDIS_URI``).
    - ``sqlite``: SQLite-backed queue (uses ``MONET_QUEUE_DB``, default
      ``.monet/queue.db``).
    """
    backend = os.getenv("MONET_QUEUE_BACKEND", "memory")
    if backend == "redis":
        from monet.core.queue_redis import RedisTaskQueue

        return RedisTaskQueue(os.environ["REDIS_URI"])  # type: ignore[return-value]
    if backend == "sqlite":
        from monet.core.queue_sqlite import SQLiteTaskQueue

        return SQLiteTaskQueue(os.getenv("MONET_QUEUE_DB", ".monet/queue.db"))  # type: ignore[return-value]
    return InMemoryTaskQueue()  # type: ignore[no-any-return]


# ── Infrastructure init (runs at import time) ───────────────────────
configure_tracing()
configure_catalogue(catalogue_from_env())

queue: TaskQueue = _create_queue()
configure_queue(queue)
configure_lazy_worker(queue)

__all__ = [
    "build_entry_graph",
    "build_execution_graph",
    "build_planning_graph",
    "queue",
]
