import datetime
import logging
from typing import Any, Literal, TypedDict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from monet.config import ClientConfig
from monet.queue import ProgressStore
from monet.server._auth import require_api_key
from monet.server.routes._common import Queue

_log = logging.getLogger("monet.server.routes.threads")

router = APIRouter()


class TranscriptEntry(BaseModel):
    """A single item in the unified chat timeline."""

    type: Literal["message", "telemetry", "interrupt"]
    timestamp: str
    data: dict[str, Any]


class TranscriptResponse(BaseModel):
    """Response for ``GET /api/v1/threads/{thread_id}/transcript``."""

    thread_id: str
    entries: list[TranscriptEntry]


class UnifiedEvent(TypedDict):
    """Internal model for merging messages and telemetry."""

    type: Literal["message", "telemetry"]
    timestamp_ms: int
    priority: int
    sequence: str  # Monotonic tie-breaker
    call_id: str | None
    parent_call_id: str | None
    data: dict[str, Any]


def _parse_iso_ms(iso: str) -> int:
    """Parse ISO8601 string to epoch milliseconds."""
    if not iso:
        return 0
    try:
        # Handle Zulu suffix and convert to UTC epoch ms
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def _get_priority(e_type: str, data: dict[str, Any]) -> int:
    """Deterministic causal priority rules.

    0 = User Input (Anchor)
    1 = Work Started
    2 = Work Progress
    3 = Work Completed
    4 = Assistant Response
    """
    if e_type == "message":
        role = data.get("role")
        return 0 if role in ("user", "human") else 4

    # Telemetry
    status = data.get("status")
    if status in ("AGENT_STARTED", "agent:started"):
        return 1
    if status in (
        "AGENT_COMPLETED",
        "agent:completed",
        "agent:failed",
        "AGENT_FAILED",
        "agent:error",
        "error",
    ):
        return 3
    return 2


@router.get(
    "/threads/{thread_id}/transcript",
    dependencies=[Depends(require_api_key)],
    response_model=TranscriptResponse,
)
async def get_thread_transcript(
    thread_id: str,
    queue: Queue,
) -> TranscriptResponse:
    """Synthesize a unified, chronological timeline using high-performance
    thread telemetry."""
    from langgraph_sdk import get_client as get_lg_client

    cfg = ClientConfig.load()
    client = get_lg_client(url=cfg.server_url)

    # 1. Fetch raw data (O(1) relative to turn count)
    telemetry: list[dict[str, Any]] = []
    history: list[Any] = []
    try:
        if isinstance(queue, ProgressStore):
            telemetry = await queue.get_thread_progress_history(thread_id)
            _log.debug(
                "Found %d telemetry events in thread stream %s",
                len(telemetry),
                thread_id,
            )

        # Verify thread existence first to provide better error messages
        try:
            await client.threads.get(thread_id)
        except Exception:
            _log.warning("Thread %s not found in LangGraph", thread_id)
            return TranscriptResponse(thread_id=thread_id, entries=[])

        history = await client.threads.get_history(thread_id, limit=100)
        _log.info(
            "Transcript synthesis for %s: %d telemetry, %d history snapshots",
            thread_id,
            len(telemetry),
            len(history),
        )
    except Exception as exc:
        _log.exception("Transcript fetch failed for %s", thread_id)
        raise HTTPException(500, f"Synthesis failed: {exc}") from exc

    # 2. Project into a Unified Event Timeline
    events: list[UnifiedEvent] = []

    # A. Normalize telemetry
    for e in telemetry:
        ts = e.get("timestamp_ms") or 0
        events.append(
            {
                "type": "telemetry",
                "timestamp_ms": int(ts),
                "priority": _get_priority("telemetry", e),
                "sequence": str(e.get("_redis_id", ts)),
                "call_id": e.get("task_id"),
                "parent_call_id": e.get("parent_call_id"),
                "data": e,
            }
        )

    # B. Normalize messages (de-duplicated by ID)
    seen_msg_ids: set[str] = set()
    seen_user_content: set[tuple[int, str]] = set()
    _msg_seq = 0
    for s_idx, snapshot in enumerate(reversed(list(history))):
        # Handle both dict-like and object-like snapshots from different SDK versions
        _snap: Any = snapshot
        s_dict: dict[str, Any] = (
            _snap
            if isinstance(_snap, dict)
            else (_snap.dict() if hasattr(_snap, "dict") else {})
        )
        created_at = s_dict.get("created_at") or getattr(snapshot, "created_at", "")

        ts_ms = _parse_iso_ms(str(created_at))
        values = s_dict.get("values", {}) or getattr(snapshot, "values", {})

        # Guard against None-values in partial checkpoints
        if values is None:
            continue

        msgs = (
            values.get("messages", [])
            if isinstance(values, dict)
            else getattr(values, "messages", [])
        )
        msgs = msgs or []

        for m_idx, m in enumerate(msgs):
            try:
                mid = m.get("id") if isinstance(m, dict) else getattr(m, "id", None)

                identity = mid or f"pos-{m_idx}"
                if identity in seen_msg_ids:
                    continue
                seen_msg_ids.add(identity)

                # Content-based dedup for user messages: update_state and
                # the subsequent graph run can assign different IDs to the
                # same user message, producing duplicates in the timeline.
                m_role = (
                    m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
                )
                m_content = (
                    m.get("content")
                    if isinstance(m, dict)
                    else getattr(m, "content", "")
                )
                if m_role == "user" and isinstance(m_content, str):
                    content_key = (m_idx, m_content)
                    if content_key in seen_user_content:
                        continue
                    seen_user_content.add(content_key)

                # Robust dict conversion
                if isinstance(m, dict):
                    m_dict = m
                elif hasattr(m, "dict") and callable(m.dict):
                    m_dict = m.dict()
                else:
                    try:
                        m_dict = dict(m)
                    except (TypeError, ValueError):
                        m_dict = {
                            "role": getattr(m, "role", "unknown"),
                            "content": getattr(m, "content", ""),
                            "id": mid,
                        }

                events.append(
                    {
                        "type": "message",
                        "timestamp_ms": ts_ms,
                        "priority": _get_priority("message", m_dict),
                        "sequence": f"msg-{_msg_seq:06d}",
                        "call_id": None,
                        "parent_call_id": None,
                        "data": m_dict,
                    }
                )
                _msg_seq += 1
            except Exception as m_exc:
                _log.warning(
                    "Skipping malformed message at index %d in snapshot %d: %s",
                    m_idx,
                    s_idx,
                    m_exc,
                )
                continue

    # 3. Causal merge: messages keep checkpoint order; interleave telemetry by timestamp
    msg_events = [e for e in events if e["type"] == "message"]
    tel_events = [e for e in events if e["type"] == "telemetry"]
    tel_events.sort(key=lambda x: (x["timestamp_ms"], x["priority"], x["sequence"]))

    merged: list[UnifiedEvent] = []
    tel_idx = 0
    for msg in msg_events:
        while (
            tel_idx < len(tel_events)
            and tel_events[tel_idx]["timestamp_ms"] < msg["timestamp_ms"]
        ):
            merged.append(tel_events[tel_idx])
            tel_idx += 1
        merged.append(msg)
    while tel_idx < len(tel_events):
        merged.append(tel_events[tel_idx])
        tel_idx += 1
    events = merged

    # 4. API Response
    entries = [
        TranscriptEntry(
            type=e["type"],
            timestamp=str(e["timestamp_ms"]),
            data=e["data"],
        )
        for e in events
    ]

    return TranscriptResponse(thread_id=thread_id, entries=entries)
