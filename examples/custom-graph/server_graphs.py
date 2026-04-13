"""Graph exports for Aegra dev / serve.

Configures infrastructure (tracing, catalogue, queue, worker) at import
time — same pattern as monet's built-in ``default_graphs.py``. Imports
the custom agents and hooks so they register into the handler registry
and hook registry respectively.

Point ``aegra.json`` here::

    {
      "dependencies": ["."],
      "graphs": {
        "review": "server_graphs:build_review_graph"
      },
      "env": ".env"
    }
"""

from __future__ import annotations

# --- Import agents so @agent decorators fire and register handlers ---
import agents.summarizer  # noqa: F401

# --- Import hooks so @on_hook decorators fire and register handlers ---
import hooks.context_injection
import hooks.output_validation  # noqa: F401

from monet.catalogue import catalogue_from_env, configure_catalogue
from monet.core.tracing import configure_tracing
from monet.orchestration import configure_queue
from monet.queue import InMemoryTaskQueue
from monet.server import configure_lazy_worker

# --- Infrastructure init (runs at import time) ---
configure_tracing()
configure_catalogue(catalogue_from_env())

_queue = InMemoryTaskQueue()
configure_queue(_queue)
configure_lazy_worker(_queue)

from graphs.review_pipeline import build_review_graph  # noqa: E402

__all__ = ["build_review_graph"]
