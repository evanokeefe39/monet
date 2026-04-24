"""Operational and health check routes."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response

from monet.server.routes._common import (
    Deployments,
    HealthResponse,
    Queue,
    monet_version,
)

router = APIRouter()


@router.get("/health")
async def health(
    request: Request,
    deployments: Deployments,
    queue: Queue,
    response: Response,
) -> HealthResponse:
    """Health check. No authentication required."""
    active = await deployments.get_active()
    worker_count = len(active)
    queued = getattr(queue, "pending_count", 0)
    start_time: float = getattr(request.app.state, "start_time", 0.0)
    uptime = time.monotonic() - start_time if start_time else 0.0
    backend = queue.backend_name
    version_str = monet_version()

    healthy = await queue.ping()
    redis_status: str | None = None
    if backend != "memory":
        redis_status = "ok" if healthy else "down"
    if not healthy:
        response.status_code = 503
        return HealthResponse(
            status="degraded",
            workers=worker_count,
            queued=queued,
            redis=redis_status,
            version=version_str,
            queue_backend=backend,
            uptime_seconds=uptime,
        )
    return HealthResponse(
        status="ok",
        workers=worker_count,
        queued=queued,
        redis=redis_status,
        version=version_str,
        queue_backend=backend,
        uptime_seconds=uptime,
    )
