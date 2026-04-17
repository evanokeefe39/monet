"""Graph exports for Aegra dev / serve.

Configures infrastructure (tracing, artifacts, queue, worker) at import
time so the custom stack boots standalone. Imports the agents module for
side-effect registration into monet's handler registry.

Point ``aegra.json`` here::

    {
      "graphs": {
        "chat": "server_graphs:build_chat_graph",
        "custom_pipeline": "server_graphs:build_custom_pipeline"
      }
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

# --- Import agents so @agent decorators fire and register handlers ---
import myco.agents  # noqa: F401

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

from myco.graphs.chat import build_chat_graph as _build_chat_graph  # noqa: E402
from myco.graphs.pipeline import (  # noqa: E402
    build_custom_pipeline as _build_custom_pipeline,
)


def build_chat_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_chat_graph()


def build_custom_pipeline() -> CompiledStateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_custom_pipeline()


__all__ = ["build_chat_graph", "build_custom_pipeline"]
