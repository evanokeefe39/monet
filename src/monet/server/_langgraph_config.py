"""Aegra / LangGraph configuration generation and merging for ``monet dev``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def default_config() -> dict[str, Any]:
    """Return the built-in Aegra config for monet's default graphs.

    This config points to the three standard graphs (entry, planning,
    execution) exported by ``monet.server.default_graphs``, and mounts
    monet's worker/task routes via the ``http.app`` custom-routes field.
    """
    return {
        "dependencies": ["."],
        "graphs": {
            "entry": "monet.server.default_graphs:build_entry_graph",
            "planning": "monet.server.default_graphs:build_planning_graph",
            "execution": "monet.server.default_graphs:build_execution_graph",
        },
        "http": {
            "app": "monet.server._aegra_routes:app",
        },
        "env": ".env",
    }


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge a user-provided config on top of the base config.

    Merge rules:
    - ``graphs``: user entries override by key, base entries preserved
    - ``dependencies``: union of both lists (deduplicated, order preserved)
    - ``http``: user value wins if present (replaces entire section)
    - ``env``: user value wins if present
    - All other keys: user value wins if present

    Args:
        base: The default config from :func:`default_config`.
        override: User-provided config loaded from ``aegra.json``
            or ``langgraph.json``.

    Returns:
        Merged configuration dict.
    """
    merged = dict(base)

    # Graphs: base entries + user overrides/additions.
    base_graphs = dict(base.get("graphs", {}))
    base_graphs.update(override.get("graphs", {}))
    merged["graphs"] = base_graphs

    # Dependencies: union, deduplicated, order preserved.
    base_deps: list[str] = list(base.get("dependencies", []))
    override_deps: list[str] = list(override.get("dependencies", []))
    seen: set[str] = set()
    merged_deps: list[str] = []
    for dep in base_deps + override_deps:
        if dep not in seen:
            seen.add(dep)
            merged_deps.append(dep)
    merged["dependencies"] = merged_deps

    # Env: user wins.
    if "env" in override:
        merged["env"] = override["env"]

    # Pass through any other user keys (e.g. "http", "auth").
    for key, value in override.items():
        if key not in ("graphs", "dependencies", "env"):
            merged[key] = value

    return merged


def write_config(config: dict[str, Any], target_dir: Path) -> Path:
    """Write an Aegra config to ``.monet/aegra.json``.

    Creates the ``.monet/`` directory if it does not exist.

    Args:
        config: The merged config dict.
        target_dir: The working directory (usually ``Path.cwd()``).

    Returns:
        Path to the written config file.
    """
    monet_dir = target_dir / ".monet"
    monet_dir.mkdir(exist_ok=True)
    config_path = monet_dir / "aegra.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return config_path
