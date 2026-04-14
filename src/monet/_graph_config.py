"""Graph role mapping and entrypoint declarations from monet.toml.

Two configs live in ``monet.toml``:

``[graphs]`` — logical role to graph ID mapping. Roles:
``entry``, ``planning``, ``execution``, ``chat`` (defaults); plus any
user-added role names for custom graphs.

``[entrypoints.<name>]`` — which graphs ``monet run`` may invoke and
how. Each declares ``graph = "<graph-id>"`` and
``kind = "pipeline" | "single" | "messages"``. Graphs NOT listed here
cannot be driven from ``monet run`` — making internal subgraphs
(``planning``, ``execution``) private by default.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal, TypedDict

__all__ = [
    "DEFAULT_ENTRYPOINTS",
    "DEFAULT_GRAPH_ROLES",
    "Entrypoint",
    "EntrypointKind",
    "load_entrypoints",
    "load_graph_roles",
]

# Single source of truth for default graph IDs.
# Matches the IDs in aegra.json.
DEFAULT_GRAPH_ROLES: dict[str, str] = {
    "entry": "entry",
    "planning": "planning",
    "execution": "execution",
    "chat": "chat",
}


EntrypointKind = Literal["pipeline", "single", "messages"]


class Entrypoint(TypedDict):
    """How ``monet run --graph <name>`` should drive a graph.

    ``graph``: the server-registered graph ID (often matches ``<name>``).
    ``kind``:
      - ``pipeline`` — the default three-graph flow (entry → planning →
        execution) with HITL plan approval. Only meaningful when
        ``graph == "entry"``.
      - ``single`` — drive the named graph once with
        ``{task, run_id, trace_id}`` input and stream its final state.
      - ``messages`` — chat-style ``{messages: [...]}`` input. Reserved
        for future chat-like entrypoints; ``monet chat`` keeps its own
        dedicated command.
    """

    graph: str
    kind: EntrypointKind


# Default entrypoints. ``planning`` and ``execution`` are deliberately
# absent — they are internal subgraphs of the pipeline, not things a user
# can call directly.
DEFAULT_ENTRYPOINTS: dict[str, Entrypoint] = {
    "default": {"graph": "entry", "kind": "pipeline"},
}


def load_graph_roles(path: Path | None = None) -> dict[str, str]:
    """Load graph role mapping from monet.toml ``[graphs]`` + env vars.

    Starts from :data:`DEFAULT_GRAPH_ROLES`, then merges all keys from
    the ``[graphs]`` section of ``monet.toml`` (not just the four
    defaults — arbitrary user-defined roles are preserved). Finally,
    each key can be overridden by a ``MONET_GRAPH_{ROLE}`` env var.

    Resolution order per role (first non-empty wins):

    1. ``monet.toml [graphs]`` section value
    2. ``MONET_GRAPH_{ROLE}`` environment variable
    3. Hardcoded default from :data:`DEFAULT_GRAPH_ROLES`

    Args:
        path: Explicit path to a ``monet.toml`` file. Defaults to
            ``Path.cwd() / "monet.toml"``.

    Returns:
        Mapping of role name to graph ID.
    """
    if path is None:
        path = Path.cwd() / "monet.toml"

    roles = DEFAULT_GRAPH_ROLES.copy()

    # Merge all keys from monet.toml [graphs] — including user-defined ones.
    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        graphs_section = raw.get("graphs", {})
        if isinstance(graphs_section, dict):
            for k, v in graphs_section.items():
                if isinstance(v, str) and v:
                    roles[k] = v

    # Env var overrides for every known key.
    for role in list(roles):
        env_val = os.environ.get(f"MONET_GRAPH_{role.upper()}", "")
        if env_val:
            roles[role] = env_val

    return roles


def load_entrypoints(path: Path | None = None) -> dict[str, Entrypoint]:
    """Load entrypoints from ``monet.toml [entrypoints.<name>]`` sections.

    Starts from :data:`DEFAULT_ENTRYPOINTS` and merges any entrypoints
    declared in ``monet.toml``. Raises ``ValueError`` if an entrypoint
    has a missing or unknown ``kind``.
    """
    if path is None:
        path = Path.cwd() / "monet.toml"

    entrypoints: dict[str, Entrypoint] = dict(DEFAULT_ENTRYPOINTS)

    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        section = raw.get("entrypoints", {})
        if isinstance(section, dict):
            for name, spec in section.items():
                if not isinstance(spec, dict):
                    continue
                graph = spec.get("graph")
                kind = spec.get("kind")
                if not isinstance(graph, str) or not graph:
                    msg = f"[entrypoints.{name}]: 'graph' must be a non-empty string"
                    raise ValueError(msg)
                if kind not in ("pipeline", "single", "messages"):
                    msg = (
                        f"[entrypoints.{name}]: 'kind' must be one of "
                        "'pipeline', 'single', 'messages'"
                    )
                    raise ValueError(msg)
                entrypoints[name] = {"graph": graph, "kind": kind}

    return entrypoints
