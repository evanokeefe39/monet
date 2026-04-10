"""Default graph exports for ``langgraph dev`` / ``langgraph build``.

Point ``langgraph.json`` here to serve the three monet graphs with
zero configuration::

    {
      "dependencies": ["."],
      "graphs": {
        "entry": "monet.server.default_graphs:build_entry_graph",
        "planning": "monet.server.default_graphs:build_planning_graph",
        "execution": "monet.server.default_graphs:build_execution_graph"
      },
      "env": ".env"
    }

Infrastructure (tracing, catalogue, queue, worker) is configured at
import time using environment defaults.  Override via env vars:

- ``MONET_CATALOGUE_DIR`` — catalogue storage path (default: ``.catalogue``)
- ``OTEL_EXPORTER_OTLP_ENDPOINT`` / ``LANGFUSE_*`` — tracing backend
"""

from __future__ import annotations

import monet.agents  # noqa: F401 — registers reference agents
from monet.catalogue import catalogue_from_env, configure_catalogue
from monet.core.tracing import configure_tracing
from monet.orchestration import (
    build_entry_graph,
    build_execution_graph,
    build_planning_graph,
    configure_queue,
)
from monet.queue import InMemoryTaskQueue
from monet.server import configure_lazy_worker

# ── Infrastructure init (runs at import time) ───────────────────────
configure_tracing()
configure_catalogue(catalogue_from_env())

_queue = InMemoryTaskQueue()
configure_queue(_queue)
configure_lazy_worker(_queue)

__all__ = ["build_entry_graph", "build_execution_graph", "build_planning_graph"]
