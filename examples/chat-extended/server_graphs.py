"""Graph entrypoints for this example's Aegra server.

Importing ``agents`` at module load fires the ``@agent`` decorators
so ``search`` and ``report_writer`` are registered in the manifest
before Aegra compiles the graphs. ``monet chat`` discovers them via
``GET /api/v1/agents`` at session start.

Serves the stock default + chat graphs — no custom graph topology
required. The point of this example is capability-level extension,
not graph-level extension (see ``custom-pipeline`` for that).
"""

from __future__ import annotations

import agents  # noqa: F401 — registers @agent capabilities

from monet.server.server_bootstrap import (
    build_chat_graph,
    build_default_graph,
)

__all__ = [
    "build_chat_graph",
    "build_default_graph",
]
