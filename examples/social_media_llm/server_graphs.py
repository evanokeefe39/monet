"""Server-side graph entry point for ``langgraph dev``.

The reference agents in :mod:`monet.agents` register themselves on
import as a side effect of the ``@agent`` decorator, populating both
the handler registry (worker-side) and the capability manifest
(orchestration-side).

This module imports ``monet.agents`` for side effects, configures
tracing and catalogue, then re-exports the three graph builders.
The example's ``langgraph.json`` points at this module.

Queue and worker setup happens at first request time via LangGraph's
async lifecycle, not at import time.
"""

from __future__ import annotations

from pathlib import Path

import monet.agents  # noqa: F401 — registers reference agents
from monet._tracing import configure_tracing
from monet.catalogue import catalogue_from_env, configure_catalogue
from monet.orchestration import (
    build_entry_graph,
    build_execution_graph,
    build_planning_graph,
)

# ── Server-side init (sync, runs at import time) ─────────────────────
configure_tracing()

_default_root = Path(__file__).resolve().parent / ".catalogue"
configure_catalogue(catalogue_from_env(default_root=_default_root))


__all__ = ["build_entry_graph", "build_execution_graph", "build_planning_graph"]
