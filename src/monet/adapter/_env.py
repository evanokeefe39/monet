from __future__ import annotations

import os
import re
from typing import Any

_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def interpolate(value: str) -> str:
    """Replace ${VAR} and ${VAR:default} with environment values."""

    def _replace(m: re.Match[str]) -> str:
        var, default = m.group(1), m.group(2)
        result = os.environ.get(var)
        if result is None:
            if default is None:
                raise KeyError(
                    f"Environment variable {var!r} not set and no default provided"
                )
            return default
        return result

    return _PATTERN.sub(_replace, value)


def interpolate_obj(obj: Any) -> Any:
    """Recursively interpolate all string values in a parsed TOML structure."""
    if isinstance(obj, str):
        return interpolate(obj)
    if isinstance(obj, dict):
        return {k: interpolate_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [interpolate_obj(v) for v in obj]
    return obj
