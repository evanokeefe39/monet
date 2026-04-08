"""Server-side graph entry point for ``langgraph dev``.

The reference agents in :mod:`monet.agents` register themselves on
import as a side effect of the ``@agent`` decorator. The orchestration
graphs (``entry``, ``planning``, ``execution``) call those agents at
runtime via :func:`monet.orchestration.invoke_agent`, which looks them
up in :data:`monet._registry.default_registry`.

That registry is populated only when ``monet.agents`` has been imported.
The SDK ``monet/__init__.py`` does **not** do that automatically — the
reference agent stack is opt-in. The langgraph dev process therefore
needs an explicit import somewhere along the path between
``langgraph.json`` and the graph builder.

This module is that path. It imports ``monet.agents`` for its
side effects, then re-exports the three builders. The example's
``langgraph.json`` points at this module rather than directly at the
``monet.orchestration`` builders, so any process that boots from the
example's ``langgraph.json`` is guaranteed to have a populated
registry by the time the first run dispatches.
"""

from __future__ import annotations

import os
from pathlib import Path

import monet.agents  # noqa: F401 — registers reference agents
from monet.catalogue import (
    CatalogueService,
    FilesystemStorage,
    SQLiteIndex,
    configure_catalogue,
)
from monet.orchestration import (
    build_entry_graph,
    build_execution_graph,
    build_planning_graph,
)

# ── Catalogue wiring (server side) ────────────────────────────────────
#
# Reference agents call ``get_catalogue().write(...)`` to persist their
# output. Without a configured backend that raises ``NotImplementedError``
# inside the @agent wrapper, which becomes an empty AgentResult and
# silently kills downstream QA. The CLI configures its own catalogue for
# reading artifact bytes back to the user; the server needs the same
# wiring so the writes actually land.
#
# Both processes resolve ``MONET_CATALOGUE_DIR`` against their own cwd,
# so when both are started from ``examples/social_media_llm/`` they
# share a single ``.catalogue/`` directory and the CLI sees the server's
# writes through the shared filesystem + SQLite index.

# Default to ``<this file's dir>/.catalogue`` rather than ``./.catalogue``
# because ``langgraph-cli`` runs the server from a generated build
# directory whose cwd is not the example dir. Relative paths would land
# in the build dir (ephemeral) and the CLI would never see the writes.
_default_root = Path(__file__).resolve().parent / ".catalogue"
_env_override = os.environ.get("MONET_CATALOGUE_DIR", "").strip()
_catalogue_root = Path(_env_override) if _env_override else _default_root
_catalogue_root.mkdir(parents=True, exist_ok=True)
configure_catalogue(
    CatalogueService(
        storage=FilesystemStorage(root=_catalogue_root / "artifacts"),
        index=SQLiteIndex(db_url=f"sqlite+aiosqlite:///{_catalogue_root / 'index.db'}"),
    )
)


__all__ = ["build_entry_graph", "build_execution_graph", "build_planning_graph"]
