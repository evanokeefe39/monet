"""Aegra graph entrypoints for the split-fleet server.

Re-exports monet's default graph builders alongside the local demo
graph so Aegra can reference them all from one file (Aegra's loader
only resolves filesystem paths, not module paths).
"""

from __future__ import annotations

from graphs.demo_graph import build_demo_graph

from monet.server.default_graphs import (
    build_chat_graph,
    build_default_graph,
)

__all__ = [
    "build_chat_graph",
    "build_default_graph",
    "build_demo_graph",
]
