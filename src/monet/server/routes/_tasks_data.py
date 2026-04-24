"""Data-plane task routes: progress recording and event queries."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from monet._ports import MAX_INLINE_PAYLOAD_BYTES
from monet.queue import ProgressStore
from monet.queue._progress import ProgressReader, ProgressWriter
from monet.server._auth import require_api_key, require_task_auth
from monet.server.routes._common import Queue, attach_trace_context

_log = logging.getLogger("monet.server.routes.tasks_data")

router = APIRouter()


def _get_progress_writer(request: Request) -> ProgressWriter | None:
    return getattr(request.app.state, "progress_writer", None)  # type: ignore[no-any-return]


def _get_progress_reader(request: Request) -> ProgressReader | None:
    return getattr(request.app.state, "progress_reader", None)  # type: ignore[no-any-return]


OptWriter = Annotated[ProgressWriter | None, Depends(_get_progress_writer)]
OptReader = Annotated[ProgressReader | None, Depends(_get_progress_reader)]


# ---------------------------------------------------------------------------
# Existing progress routes (queue-backed, kept for backward compat)
# ---------------------------------------------------------------------------


@router.post(
    "/tasks/{task_id}/progress",
    status_code=202,
    dependencies=[Depends(require_task_auth), Depends(attach_trace_context)],
)
async def post_progress(
    task_id: str,
    body: dict[str, Any],
    queue: Queue,
    request: Request,
) -> dict[str, str]:
    """Fire-and-forget progress event from a remote worker."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            size = int(content_length)
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length") from exc
        if size > MAX_INLINE_PAYLOAD_BYTES:
            raise HTTPException(
                413,
                f"Progress payload {size} bytes exceeds "
                f"MAX_INLINE_PAYLOAD_BYTES={MAX_INLINE_PAYLOAD_BYTES}",
            )
    await queue.publish_progress(task_id, body)
    return {"status": "accepted"}


@router.get(
    "/runs/{run_id}/progress",
    dependencies=[Depends(require_api_key)],
)
async def get_run_progress(
    run_id: str,
    queue: Queue,
    count: int = Query(default=1000, ge=1, le=10000),
) -> dict[str, Any]:
    """Retrieve persisted progress events for a run (queue-backed)."""
    if not isinstance(queue, ProgressStore):
        raise HTTPException(501, "Backend does not support progress history")
    events = await queue.get_progress_history(run_id, count=count)
    return {"run_id": run_id, "events": events}


@router.get(
    "/progress",
    dependencies=[Depends(require_api_key)],
)
async def get_batch_progress(
    queue: Queue,
    run_ids: str = Query(..., description="Comma-separated run IDs"),
    count: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """Retrieve persisted progress events for multiple runs (queue-backed)."""
    if not isinstance(queue, ProgressStore):
        raise HTTPException(501, "Backend does not support progress history")
    ids = [r.strip() for r in run_ids.split(",") if r.strip()]
    results: dict[str, list[dict[str, Any]]] = {}
    for rid in ids[:50]:
        events = await queue.get_progress_history(rid, count=count)
        if events:
            results[rid] = events
    return {"progress": results}


# ---------------------------------------------------------------------------
# Phase-3 typed event routes (active when ProgressWriter/Reader are wired)
# ---------------------------------------------------------------------------


class RecordEventRequest(BaseModel):
    """Body for ``POST /runs/{run_id}/events``."""

    task_id: str
    agent_id: str
    event_type: str
    timestamp_ms: int
    trace_id: str = ""
    payload: dict[str, Any] = {}


@router.post(
    "/runs/{run_id}/events",
    status_code=202,
    dependencies=[Depends(require_api_key), Depends(attach_trace_context)],
)
async def record_run_event(
    run_id: str,
    body: RecordEventRequest,
    writer: OptWriter,
    reader: OptReader,
) -> dict[str, Any]:
    """Record a typed progress event for a run."""
    if writer is None:
        raise HTTPException(501, "No ProgressWriter configured")
    from monet.queue._progress import EventType, ProgressEvent

    event_type = EventType(body.event_type)

    if event_type == EventType.HITL_DECISION:
        cause_id = body.payload.get("cause_id") if body.payload else None
        if not cause_id:
            raise HTTPException(400, "hitl_decision must include payload.cause_id")
        if reader is None:
            raise HTTPException(501, "No ProgressReader configured")
        if not await reader.has_cause(run_id, cause_id):
            raise HTTPException(400, "hitl_decision must reference a known hitl_cause")
        if await reader.has_decision(run_id, cause_id):
            raise HTTPException(409, "hitl_decision for this cause_id already recorded")

    event: ProgressEvent = {
        "event_id": 0,
        "run_id": run_id,
        "task_id": body.task_id,
        "agent_id": body.agent_id,
        "event_type": event_type,
        "timestamp_ms": body.timestamp_ms,
    }
    if body.trace_id:
        event["trace_id"] = body.trace_id
    if body.payload:
        event["payload"] = body.payload
    event_id = await writer.record(run_id, event)
    return {"event_id": event_id}


@router.get(
    "/runs/{run_id}/events",
    dependencies=[Depends(require_api_key)],
)
async def query_run_events(
    run_id: str,
    reader: OptReader,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Query typed progress events for a run."""
    if reader is None:
        raise HTTPException(501, "No ProgressReader configured")
    events = await reader.query(run_id, after=after, limit=limit)
    return {"run_id": run_id, "events": events, "count": len(events)}


@router.get(
    "/runs/{run_id}/events/stream",
    dependencies=[Depends(require_api_key)],
)
async def stream_run_events(
    run_id: str,
    reader: OptReader,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    """Stream typed progress events for a run as Server-Sent Events.

    Each SSE message carries ``id: <event_id>`` so browser EventSource
    reconnects via Last-Event-ID header automatically. Callers reconnect
    with ``?after=<last_event_id>`` to resume without duplicates.
    """
    if reader is None:
        raise HTTPException(501, "No ProgressReader configured")

    async def _generate() -> Any:
        try:
            async for event in reader.stream(run_id, after=after):
                event_id = event.get("event_id", 0)
                data = json.dumps(event, default=str)
                yield f"id: {event_id}\ndata: {data}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
