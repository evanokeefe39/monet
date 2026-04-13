"""Graph role mapping from monet.toml + environment variables.

Maps logical graph roles to server-registered graph IDs. The mapping
is a plain ``dict[str, str]`` — extensible by config alone. Self-hosting
users add arbitrary keys to ``monet.toml [graphs]`` without code changes.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

__all__ = ["DEFAULT_GRAPH_ROLES", "load_graph_roles"]

# Single source of truth for default graph IDs.
# Matches the IDs in aegra.json.
DEFAULT_GRAPH_ROLES: dict[str, str] = {
    "entry": "entry",
    "planning": "planning",
    "execution": "execution",
    "chat": "chat",
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
