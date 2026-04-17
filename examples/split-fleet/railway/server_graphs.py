"""Aegra graph entrypoints for the split-fleet Railway server service.

Re-exports monet's default graph builders plus the local demo graph.
Aegra's loader resolves filesystem paths only, so this file is how
``aegra.json`` points at the compiled graphs.
"""

from __future__ import annotations

from graphs.demo_graph import build_demo_graph

from monet.server.server_bootstrap import (
    build_chat_graph,
    build_default_graph,
)

__all__ = [
    "build_chat_graph",
    "build_default_graph",
    "build_demo_graph",
]
