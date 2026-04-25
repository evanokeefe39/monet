"""Control-plane task routes: claim, complete, fail."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from monet.events import EventType, ProgressEvent
from monet.progress import ProgressWriter
from monet.server._auth import require_api_key, require_task_auth
from monet.server._event_router import EventPolicy, classify_event
from monet.server.routes._common import CapIndex, Queue, attach_trace_context
from monet.types import AgentResult, Signal, build_artifact_pointer

_log = logging.getLogger("monet.server.routes.tasks_control")

router = APIRouter()


def _get_progress_writer(request: Request) -> ProgressWriter | None:
    return getattr(request.app.state, "progress_writer", None)  # type: ignore[no-any-return]


OptWriter = Annotated[ProgressWriter | None, Depends(_get_progress_writer)]


async def _route_event(
    writer: ProgressWriter | None,
    event: ProgressEvent,
) -> None:
    """Record event according to its routing policy. Best-effort — never raises."""
    if writer is None:
        return
    policy = classify_event(event)
    if policy in (EventPolicy.DUAL_ROUTED, EventPolicy.SILENT_AUDIT):
        try:
            await writer.record(event["run_id"], event)
        except Exception:
            _log.exception(
                "progress_writer.record failed for task=%s event_type=%s",
                event["task_id"],
                event["event_type"],
            )


class TaskCompleteRequest(BaseModel):
    """Body for ``POST /api/v1/tasks/{task_id}/complete``."""

    success: bool
    output: str | dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    trace_id: str = ""
    run_id: str = ""
    agent_id: str = ""


class TaskFailRequest(BaseModel):
    """Body for ``POST /api/v1/tasks/{task_id}/fail``."""

    error: str
    run_id: str = ""
    agent_id: str = ""


class PoolClaimRequest(BaseModel):
    """Body for ``POST /api/v1/pools/{pool}/claim``."""

    consumer_id: str
    block_ms: int = 5000


@router.post(
    "/pools/{pool}/claim",
    dependencies=[Depends(require_api_key)],
)
async def claim_from_pool(
    pool: str,
    body: PoolClaimRequest,
    response: Response,
    queue: Queue,
    cap_index: CapIndex,
) -> dict[str, Any] | None:
    """Claim one task from the pool, server-blocking up to ``block_ms``."""
    if not cap_index.worker_for_pool(body.consumer_id, pool):
        raise HTTPException(
            403,
            f"worker {body.consumer_id!r} is not heartbeating for pool {pool!r}",
        )
    record = await queue.claim(
        pool, consumer_id=body.consumer_id, block_ms=body.block_ms
    )
    if record is None:
        response.status_code = 204
        return None
    return dict(record)


@router.post(
    "/tasks/{task_id}/complete",
    dependencies=[Depends(require_task_auth), Depends(attach_trace_context)],
)
async def complete_task(
    task_id: str,
    body: TaskCompleteRequest,
    queue: Queue,
    writer: OptWriter,
) -> dict[str, str]:
    """Post a successful result for a claimed task."""
    result = AgentResult(
        success=body.success,
        output=body.output,
        artifacts=tuple(build_artifact_pointer(a) for a in body.artifacts),
        signals=tuple(
            Signal(
                type=s.get("type", ""),
                reason=s.get("reason", ""),
                metadata=s.get("metadata"),
            )
            for s in body.signals
        ),
        trace_id=body.trace_id,
        run_id=body.run_id,
    )
    await queue.complete(task_id, result)

    event: ProgressEvent = {
        "event_id": 0,
        "run_id": body.run_id,
        "task_id": task_id,
        "agent_id": body.agent_id,
        "event_type": EventType.AGENT_COMPLETED,
        "timestamp_ms": int(time.time() * 1000),
    }
    if body.trace_id:
        event["trace_id"] = body.trace_id
    await _route_event(writer, event)

    return {"status": "ok"}


@router.post(
    "/tasks/{task_id}/fail",
    dependencies=[Depends(require_task_auth), Depends(attach_trace_context)],
)
async def fail_task(
    task_id: str,
    body: TaskFailRequest,
    queue: Queue,
    writer: OptWriter,
) -> dict[str, str]:
    """Post a failure for a claimed task."""
    await queue.fail(task_id, body.error)

    event: ProgressEvent = {
        "event_id": 0,
        "run_id": body.run_id,
        "task_id": task_id,
        "agent_id": body.agent_id,
        "event_type": EventType.AGENT_FAILED,
        "timestamp_ms": int(time.time() * 1000),
        "payload": {"error": body.error},
    }
    await _route_event(writer, event)

    return {"status": "ok"}
