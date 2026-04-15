"""Graph role mapping and entrypoint declarations from monet.toml.

Two configs live in ``monet.toml``:

``[graphs]`` — logical role to graph ID mapping. Roles:
``entry``, ``planning``, ``execution``, ``chat`` (defaults); plus any
user-added role names for custom graphs.

``[entrypoints.<name>]`` — which graphs ``monet run`` may invoke. Each
declares ``graph = "<graph-id>"``. Graphs NOT listed here cannot be
driven from ``monet run`` — making internal subgraphs (``planning``,
``execution``) private by default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from ._env import graph_role_env, read_str
from ._load import default_config_path, read_toml

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DEFAULT_ENTRYPOINTS",
    "DEFAULT_GRAPH_ROLES",
    "Entrypoint",
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


class Entrypoint(TypedDict):
    """An allow-list entry declaring that ``graph`` is invocable via ``monet run``.

    ``graph``: the server-registered graph ID (often matches ``<name>``).
    """

    graph: str


# Default entrypoints. ``planning`` and ``execution`` are deliberately
# absent — they are internal subgraphs of the default pipeline, not
# things a user can call directly. ``monet run`` (no ``--graph``) uses
# the ``default`` entrypoint; ``monet chat`` (no ``--graph``) uses
# ``chat``. Override either by adding a ``[entrypoints.<name>]`` block
# to ``monet.toml``.
DEFAULT_ENTRYPOINTS: dict[str, Entrypoint] = {
    "default": {"graph": "entry"},
    "chat": {"graph": "chat"},
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
    resolved = path if path is not None else default_config_path()

    roles = DEFAULT_GRAPH_ROLES.copy()

    # Merge all keys from monet.toml [graphs] — including user-defined ones.
    raw = read_toml(resolved)
    graphs_section = raw.get("graphs", {})
    if isinstance(graphs_section, dict):
        for k, v in graphs_section.items():
            if isinstance(v, str) and v:
                roles[k] = v

    # Env var overrides for every known key.
    for role in list(roles):
        env_val = read_str(graph_role_env(role))
        if env_val:
            roles[role] = env_val

    return roles


def load_entrypoints(path: Path | None = None) -> dict[str, Entrypoint]:
    """Load entrypoints from ``monet.toml [entrypoints.<name>]`` sections.

    Starts from :data:`DEFAULT_ENTRYPOINTS` and merges any entrypoints
    declared in ``monet.toml``. Raises ``ValueError`` if an entrypoint
    has a missing ``graph`` field.
    """
    resolved = path if path is not None else default_config_path()

    entrypoints: dict[str, Entrypoint] = dict(DEFAULT_ENTRYPOINTS)

    raw = read_toml(resolved)
    section = raw.get("entrypoints", {})
    if isinstance(section, dict):
        for name, spec in section.items():
            if not isinstance(spec, dict):
                continue
            graph = spec.get("graph")
            if not isinstance(graph, str) or not graph:
                msg = f"[entrypoints.{name}]: 'graph' must be a non-empty string"
                raise ValueError(msg)
            entrypoints[name] = {"graph": graph}

    return entrypoints
