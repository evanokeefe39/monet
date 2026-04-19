"""Normalize JSONL event streams so Python and Go outputs can be diffed.

Masks server-generated identifiers (run_id, task_id, checkpoint_id,
thread_id), RFC 3339 timestamps, and uptime-like counters — none of
which should differ semantically between client implementations.

The result is still JSONL but with stable placeholders, so a byte-level
diff highlights only true behavioral divergences.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_HEX_TOKEN_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_RFC3339_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)

_MASKED_KEYS = frozenset(
    {
        "run_id",
        "task_id",
        "checkpoint_id",
        "thread_id",
        "created_at",
        "updated_at",
        "uptime_seconds",
    }
)


def _mask_scalar(value: Any, key: str | None) -> Any:
    if key in _MASKED_KEYS:
        if value is None or value == "":
            return value
        return f"<masked:{key}>"
    if isinstance(value, str):
        value = unicodedata.normalize("NFC", value)
        value = _UUID_RE.sub("<uuid>", value)
        value = _RFC3339_RE.sub("<ts>", value)
        value = _HEX_TOKEN_RE.sub("<hex>", value)
    return value


def _walk(node: Any, key: str | None = None) -> Any:
    if isinstance(node, dict):
        return {k: _walk(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk(v, key) for v in node]
    return _mask_scalar(node, key)


def normalize_event(record: dict[str, Any]) -> dict[str, Any]:
    return _walk(record)  # type: ignore[no-any-return]


def normalize_stream(text: str) -> list[dict[str, Any]]:
    """Parse JSONL text and return a list of normalized event dicts."""
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.append({"_raw": line})
            continue
        out.append(normalize_event(rec))
    return out
