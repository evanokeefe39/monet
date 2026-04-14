"""Shared ``monet.toml`` reader.

Every config loader previously reimplemented
``path = Path.cwd() / "monet.toml"; tomllib.load(f)``. This module is the
one place that logic lives.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ._env import MONET_CONFIG_PATH, read_path

__all__ = [
    "default_config_path",
    "read_toml",
    "read_toml_section",
]


def default_config_path() -> Path:
    """Return the ``monet.toml`` path to use when a caller passes ``None``.

    Resolution order:

    1. ``MONET_CONFIG_PATH`` environment variable (absolute or relative).
    2. ``Path.cwd() / "monet.toml"``.
    """
    env_override = read_path(MONET_CONFIG_PATH)
    if env_override is not None:
        return env_override
    return Path.cwd() / "monet.toml"


def read_toml(path: Path | None = None) -> dict[str, Any]:
    """Read ``monet.toml`` and return its parsed mapping.

    Args:
        path: Explicit path. ``None`` resolves via
            :func:`default_config_path`.

    Returns:
        Parsed TOML as a dict. Returns an empty dict when the file does
        not exist, so callers can treat "file absent" and "file present
        but section missing" uniformly.
    """
    resolved = path if path is not None else default_config_path()
    if not resolved.exists():
        return {}
    with open(resolved, "rb") as f:
        return tomllib.load(f)


def read_toml_section(section: str, path: Path | None = None) -> dict[str, Any]:
    """Read a single top-level section from ``monet.toml``.

    Returns an empty dict if the file is missing, the section is absent,
    or the section is not a TOML table. Callers that require the section
    to be well-formed should raise themselves on the returned shape.
    """
    raw = read_toml(path)
    value = raw.get(section, {})
    if not isinstance(value, dict):
        return {}
    return value
