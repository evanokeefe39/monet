"""Task claiming, completion, and progress tracking routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from monet._ports import MAX_INLINE_PAYLOAD_BYTES
from monet.queue import ProgressStore
from monet.server._auth import require_api_key, require_task_auth
from monet.server.routes._common import CapIndex, Queue  # noqa: TC001
from monet.types import AgentResult, Signal, build_artifact_pointer

_log = logging.getLogger("monet.server.routes.tasks")

router = APIRouter()


class TaskCompleteRequest(BaseModel):
    """Body for ``POST /api/v1/tasks/{task_id}/complete``."""

    success: bool
    output: str | dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    trace_id: str = ""
    run_id: str = ""


class TaskFailRequest(BaseModel):
    """Body for ``POST /api/v1/tasks/{task_id}/fail``."""

    error: str


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
    dependencies=[Depends(require_task_auth)],
)
async def complete_task(
    task_id: str,
    body: TaskCompleteRequest,
    queue: Queue,
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
    return {"status": "ok"}


@router.post(
    "/tasks/{task_id}/fail",
    dependencies=[Depends(require_task_auth)],
)
async def fail_task(
    task_id: str,
    body: TaskFailRequest,
    queue: Queue,
) -> dict[str, str]:
    """Post a failure for a claimed task."""
    await queue.fail(task_id, body.error)
    return {"status": "ok"}


@router.post(
    "/tasks/{task_id}/progress",
    status_code=202,
    dependencies=[Depends(require_task_auth)],
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
    """Retrieve persisted progress events for a run."""
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
    """Retrieve persisted progress events for multiple runs in one call."""
    if not isinstance(queue, ProgressStore):
        raise HTTPException(501, "Backend does not support progress history")
    ids = [r.strip() for r in run_ids.split(",") if r.strip()]
    results: dict[str, list[dict[str, Any]]] = {}
    for rid in ids[:50]:
        events = await queue.get_progress_history(rid, count=count)
        if events:
            results[rid] = events
    return {"progress": results}
