"""Aegra-compatible entry point for the built-in chat graph.

Aegra's graph loader (``langgraph_service.py``) uses
``importlib.util.spec_from_file_location`` to load graph modules and
re-parents them under a synthetic ``aegra_graphs.*`` namespace.  Any
module loaded this way that contains a *relative* import (``from . import
…``) will fail because the synthetic parent package does not exist.

This module is intentionally a *flat file* that uses only *absolute*
imports, so Aegra can load it safely via the file-path mechanism.

``ChatConfig._DEFAULT_CHAT_GRAPH`` points here, not at
``monet.orchestration.prebuilt.chat.__init__`` (which uses relative imports).
Custom graph implementations supplied via ``MONET_CHAT_GRAPH`` or
``[chat] graph`` in ``monet.toml`` must observe the same constraint:
their entry module must use absolute imports only.

Do NOT convert these imports to relative — that is the whole point.
"""

from __future__ import annotations

from monet.orchestration.prebuilt.chat import (
    MAX_FOLLOWUP_ATTEMPTS,
    ChatState,
    ChatTriageResult,
    build_chat_graph,
)

__all__ = [
    "MAX_FOLLOWUP_ATTEMPTS",
    "ChatState",
    "ChatTriageResult",
    "build_chat_graph",
]
