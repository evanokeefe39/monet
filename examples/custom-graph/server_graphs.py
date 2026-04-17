"""Graph exports for Aegra dev / serve.

Configures infrastructure (tracing, artifacts, queue, worker) at import
time — same pattern as monet's built-in ``server_bootstrap.py``. Imports
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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.graph import StateGraph

# --- Import agents so @agent decorators fire and register handlers ---
import agents.summarizer  # noqa: F401

# --- Import hooks so @on_hook decorators fire and register handlers ---
import hooks.context_injection
import hooks.output_validation  # noqa: F401

from monet.artifacts import artifacts_from_env, configure_artifacts
from monet.core.tracing import configure_tracing
from monet.orchestration import configure_queue
from monet.queue import InMemoryTaskQueue
from monet.server import configure_lazy_worker

# --- Infrastructure init (runs at import time) ---
configure_tracing()
configure_artifacts(artifacts_from_env())

_queue = InMemoryTaskQueue()
configure_queue(_queue)
configure_lazy_worker(_queue)

from graphs.review_pipeline import (  # noqa: E402
    build_review_graph as _build_review_graph,
)


# Aegra's factory classifier treats a 1-arg function as a config-accepting
# factory. The real builder accepts optional hooks, so wrap as 0-arg.
def build_review_graph() -> StateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_review_graph()


__all__ = ["build_review_graph"]
