"""Shared serialization helpers for queue backends.

Provides JSON serialization/deserialization for AgentResult and
TaskRecord, plus common time utilities. The Redis Streams backend and
any third-party backend use these to ensure a single source of truth
for the wire format.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from monet.types import AgentResult, Signal, build_artifact_pointer

if TYPE_CHECKING:
    from monet.queue import TaskRecord

__all__ = [
    "deserialize_result",
    "deserialize_task_record",
    "now_iso",
    "safe_parse_context",
    "serialize_result",
    "serialize_task_record",
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
        artifacts=tuple(build_artifact_pointer(a) for a in d.get("artifacts", ())),
        signals=tuple(
            Signal(type=s["type"], reason=s["reason"], metadata=s.get("metadata"))
            for s in d.get("signals", ())
        ),
        trace_id=d.get("trace_id", ""),
        run_id=d.get("run_id", ""),
    )


def serialize_task_record(record: TaskRecord) -> str:
    """Serialize a TaskRecord to a JSON string for queue storage.

    The embedded ``AgentResult`` (if present) is serialised via
    :func:`serialize_result` so enum/datetime/tuple shapes survive the
    round-trip; all other fields are JSON-native.
    """
    result = record.get("result")
    return json.dumps(
        {
            "task_id": record["task_id"],
            "agent_id": record["agent_id"],
            "command": record["command"],
            "pool": record["pool"],
            "context": record["context"],
            "status": str(record["status"]),
            "result": serialize_result(result) if result is not None else None,
            "created_at": record["created_at"],
            "claimed_at": record["claimed_at"],
            "completed_at": record["completed_at"],
        }
    )


def deserialize_task_record(raw: str) -> TaskRecord:
    """Deserialize a TaskRecord from a JSON string.

    Raises:
        json.JSONDecodeError: if ``raw`` is not valid JSON.
        KeyError: if required fields are missing.
    """
    from monet.queue import TaskStatus

    d: dict[str, Any] = json.loads(raw)
    raw_result = d.get("result")
    record: dict[str, Any] = {
        "task_id": d["task_id"],
        "agent_id": d["agent_id"],
        "command": d["command"],
        "pool": d["pool"],
        "context": d["context"],
        "status": TaskStatus(d["status"]),
        "result": deserialize_result(raw_result) if raw_result else None,
        "created_at": d["created_at"],
        "claimed_at": d.get("claimed_at"),
        "completed_at": d.get("completed_at"),
    }
    return cast("TaskRecord", record)


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
