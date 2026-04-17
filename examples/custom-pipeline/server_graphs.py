"""Aegra entrypoints. Exposes a user-defined compound graph as
``reviewed`` alongside monet's stock ``chat`` and ``default`` graphs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphs.reviewed import build_reviewed_graph as _build_reviewed_graph

from monet.server.server_bootstrap import (
    build_chat_graph,
    build_default_graph,
)

if TYPE_CHECKING:
    from langgraph.graph import StateGraph


def build_reviewed_graph() -> StateGraph:  # type: ignore[type-arg]
    """0-arg wrapper for Aegra compatibility."""
    return _build_reviewed_graph()


__all__ = [
    "build_chat_graph",
    "build_default_graph",
    "build_reviewed_graph",
]
