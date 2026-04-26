"""Graph exports for Aegra dev / serve.

Configures infrastructure at import time and imports the recruitment
agents and hook for side-effect registration. Reuses monet's built-in
default and execution graphs — no custom graphs are needed for this
example; planning + execution compose the capability agents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

# Register the two capability agents. Per-invocation outcomes live on
# OTel spans (decorator-owned) + the artifact index, so there is no
# after_agent hook — the earlier RunSummary-artifact pattern was a
# duplicate of already-captured data.
import recruitment.agents  # noqa: F401 — @agent side-effects

from monet.artifacts import artifacts_from_env, configure_artifacts
from monet.core.tracing import configure_tracing
from monet.orchestration import configure_queue
from monet.queue import InMemoryTaskQueue
from monet.server.server_bootstrap import (
    build_default_graph as _build_default_graph,
)
from monet.server.server_bootstrap import (
    build_execution_graph as _build_execution_graph,
)

configure_tracing()
configure_artifacts(artifacts_from_env())

_queue = InMemoryTaskQueue()
configure_queue(_queue)


def build_default_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_default_graph()


def build_execution_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_execution_graph()


__all__ = ["build_default_graph", "build_execution_graph"]
