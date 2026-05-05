from __future__ import annotations

import re
from typing import Any

_INDEX_RE = re.compile(r"^(\w+)\[(\d+)\]$")


def _parse(path: str) -> list[str | int]:
    if not path.startswith("$."):
        raise ValueError(f"Path must start with '$.' — got {path!r}")
    parts = path[2:].split(".")
    result: list[str | int] = []
    for part in parts:
        m = _INDEX_RE.match(part)
        if m:
            result.append(m.group(1))
            result.append(int(m.group(2)))
        else:
            result.append(part)
    return result


def extract(obj: Any, path: str) -> Any:
    """Extract value at dot-path from obj. Supports $.field.nested and $.arr[0]."""
    for key in _parse(path):
        obj = obj[key]
    return obj


def assign(obj: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Set value at dot-path in obj (mutates and returns obj)."""
    keys = _parse(path)
    current: Any = obj
    for key in keys[:-1]:
        if isinstance(key, int):
            current = current[key]
        else:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]
    current[keys[-1]] = value
    return obj
