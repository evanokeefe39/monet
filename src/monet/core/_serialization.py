"""Shared serialization helpers for queue backends.

Provides JSON serialization/deserialization for AgentResult and common
time utilities. All queue backends (Redis, SQLite, Upstash) use these
to ensure a single source of truth for the wire format.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from monet.types import AgentResult, ArtifactPointer, Signal

__all__ = [
    "deserialize_result",
    "now_iso",
    "safe_parse_context",
    "serialize_result",
]

_log = logging.getLogger(__name__)


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def serialize_result(r: AgentResult) -> str:
    """Serialize an AgentResult to a JSON string for queue storage."""
    return json.dumps(
        {
            "success": r.success,
            "output": r.output,
            "artifacts": [dict(a) for a in r.artifacts],
            "signals": [dict(s) for s in r.signals],
            "trace_id": r.trace_id,
            "run_id": r.run_id,
        }
    )


def deserialize_result(raw: str) -> AgentResult:
    """Deserialize an AgentResult from a JSON string.

    Raises:
        json.JSONDecodeError: If ``raw`` is not valid JSON.
        KeyError: If required fields are missing.
    """
    d: dict[str, Any] = json.loads(raw)
    return AgentResult(
        success=d["success"],
        output=d.get("output"),
        artifacts=tuple(
            ArtifactPointer(artifact_id=a["artifact_id"], url=a["url"])
            for a in d.get("artifacts", ())
        ),
        signals=tuple(
            Signal(type=s["type"], reason=s["reason"], metadata=s.get("metadata"))
            for s in d.get("signals", ())
        ),
        trace_id=d.get("trace_id", ""),
        run_id=d.get("run_id", ""),
    )


def safe_parse_context(raw: str | None, *, source: str = "") -> dict[str, Any] | None:
    """Parse a JSON context string, returning None on failure.

    Logs a warning with the source location when parsing fails,
    instead of crashing the caller.

    Args:
        raw: JSON string to parse, or None.
        source: Human-readable label for log messages (e.g. "redis.fail").

    Returns:
        Parsed dict, or None if raw is None or unparseable.
    """
    if raw is None:
        return None
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, TypeError):
        preview = raw[:200] if isinstance(raw, str) else repr(raw)[:200]
        _log.warning("Corrupt context JSON in %s: %s", source or "unknown", preview)
        return None
