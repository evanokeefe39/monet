"""Aegra / LangGraph configuration generation and merging for ``monet dev``."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def default_config() -> dict[str, Any]:
    """Return the built-in Aegra config for monet's default graphs.

    Serves two graphs: ``default`` (the compound planning→execution
    pipeline) and ``chat`` (the multi-turn conversational graph). The
    chat graph's dotted path is resolved from :class:`ChatConfig` — set
    ``MONET_CHAT_GRAPH`` or ``[chat] graph`` in ``monet.toml`` to swap
    in an agentic implementation. Also mounts monet's worker/task
    routes via the ``http.app`` custom-routes field.
    """
    from monet.config import ChatConfig

    return {
        "dependencies": ["."],
        "graphs": {
            "chat": ChatConfig.load().graph,
            "default": "monet.server.server_bootstrap:build_default_graph",
            "execution": "monet.server.server_bootstrap:build_execution_graph",
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


def _resolve_graph_paths(config: dict[str, Any], config_dir: Path) -> dict[str, Any]:
    """Resolve module-style graph references to relative file paths.

    Aegra's graph loader only supports filesystem paths and splits on
    the first ``:``, so absolute Windows paths (``C:\\...``) break the
    parser.  This converts module-style entries like
    ``monet.server.server_bootstrap:build_chat_graph`` to a POSIX
    relative path from *config_dir* (the ``.monet/`` directory where
    ``aegra.json`` lives), e.g. ``../src/monet/server/server_bootstrap.py``.

    File-style references (ending in ``.py`` or starting with ``./``
    / ``../``) are left unchanged.
    """
    from pathlib import PurePosixPath

    graphs = config.get("graphs")
    if not graphs:
        return config

    # Ensure cwd is on sys.path before we start importing modules. Aegra
    # will later add everything in aegra.json ``dependencies`` (which
    # includes ``.``), but resolution here runs before Aegra boots.
    # Without this, a user's chat graph module referenced from
    # ``monet.toml [chat]`` fails to import when ``server_bootstrap``
    # runs its module-level ``validate_for_boot``. Mirrors Aegra's
    # runtime sys.path layout so dev and serve see the same import
    # environment.
    cwd_str = str(Path.cwd())
    if cwd_str not in sys.path:
        sys.path.insert(0, cwd_str)

    resolved = dict(config)
    resolved_graphs: dict[str, str] = {}
    for graph_id, ref in graphs.items():
        if ":" not in ref:
            resolved_graphs[graph_id] = ref
            continue
        module_part, export = ref.rsplit(":", 1)
        # Already a file path — leave it alone.
        if module_part.endswith(".py") or module_part.startswith(("./", "../", "/")):
            resolved_graphs[graph_id] = ref
            continue
        # Resolve the module to an absolute .py path.  Try import first
        # (works for installed packages like monet.server.server_bootstrap),
        # then fall back to looking for a local .py file (works for user
        # scripts like ``server_graphs`` in the working directory).
        abs_path: Path | None = None
        try:
            mod = importlib.import_module(module_part)
            if mod.__file__ is not None:
                abs_path = Path(mod.__file__).resolve()
        except ModuleNotFoundError:
            pass

        if abs_path is None:
            # Try as a local file: module.sub → module/sub.py
            candidate = Path.cwd() / (module_part.replace(".", os.sep) + ".py")
            if candidate.exists():
                abs_path = candidate.resolve()

        if abs_path is None:
            raise ValueError(
                f"Cannot resolve '{module_part}' to a file path. "
                f"Not importable as a module and "
                f"'{module_part.replace('.', os.sep)}.py' not found in {Path.cwd()}."
            )

        # Build a relative path from the config dir so the reference
        # never contains a Windows drive letter (which Aegra's `:`
        # split would misparse).
        rel_path = PurePosixPath(os.path.relpath(abs_path, config_dir.resolve()))
        resolved_graphs[graph_id] = f"{rel_path}:{export}"
    resolved["graphs"] = resolved_graphs
    return resolved


def write_config(config: dict[str, Any], target_dir: Path) -> Path:
    """Write an Aegra config to ``.monet/aegra.json``.

    Creates the ``.monet/`` directory if it does not exist.  Module-style
    graph references are resolved to absolute file paths before writing,
    since Aegra's graph loader only supports filesystem paths.

    Args:
        config: The merged config dict.
        target_dir: The working directory (usually ``Path.cwd()``).

    Returns:
        Path to the written config file.
    """
    monet_dir = target_dir / ".monet"
    config = _resolve_graph_paths(config, monet_dir)
    monet_dir.mkdir(exist_ok=True)
    config_path = monet_dir / "aegra.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return config_path
