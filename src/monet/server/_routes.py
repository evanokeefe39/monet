"""REST API routes for the monet orchestration server."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from monet._ports import MAX_INLINE_PAYLOAD_BYTES
from monet.core.manifest import AgentCapability, AgentManifest
from monet.queue import TaskQueue
from monet.queue.backends.redis_streams import RedisStreamsTaskQueue
from monet.server._auth import require_api_key, require_task_auth
from monet.server._deployment import DeploymentStore
from monet.types import AgentResult, Signal, build_artifact_pointer

__all__ = ["router"]


# -- Dependency injection helpers ------------------------------------------


def get_queue(request: Request) -> TaskQueue:
    """Retrieve the task queue from application state."""
    return request.app.state.queue  # type: ignore[no-any-return]


def get_deployments(request: Request) -> DeploymentStore:
    """Retrieve the deployment store from application state."""
    return request.app.state.deployments  # type: ignore[no-any-return]


def get_manifest(request: Request) -> AgentManifest:
    """Retrieve the agent manifest from application state."""
    return request.app.state.manifest  # type: ignore[no-any-return]


# Type aliases for annotated dependencies
Queue = Annotated[TaskQueue, Depends(get_queue)]
Deployments = Annotated[DeploymentStore, Depends(get_deployments)]
Manifest = Annotated[AgentManifest, Depends(get_manifest)]


# -- Request / Response schemas --------------------------------------------


class WorkerRegisterRequest(BaseModel):
    """Body for ``POST /api/v1/worker/register``."""

    pool: str
    capabilities: list[dict[str, str]]
    worker_id: str


class WorkerRegisterResponse(BaseModel):
    """Response for ``POST /api/v1/worker/register``."""

    deployment_id: str


class HeartbeatRequest(BaseModel):
    """Body for ``POST /api/v1/worker/heartbeat``."""

    worker_id: str
    pool: str
    capabilities: list[dict[str, str]] | None = None


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


class CreateDeploymentRequest(BaseModel):
    """Body for ``POST /api/v1/deployments``."""

    pool: str
    capabilities: list[dict[str, str]]


class PoolClaimRequest(BaseModel):
    """Body for ``POST /api/v1/pools/{pool}/claim``."""

    consumer_id: str
    block_ms: int = 5000


class HealthResponse(BaseModel):
    """Response for ``GET /api/v1/health``."""

    status: str
    workers: int
    queued: int
    redis: str | None = None


# -- Router ----------------------------------------------------------------


router = APIRouter(prefix="/api/v1")


@router.post(
    "/worker/register",
    response_model=WorkerRegisterResponse,
    dependencies=[Depends(require_api_key)],
)
async def register_worker(
    body: WorkerRegisterRequest,
    deployments: Deployments,
    manifest: Manifest,
) -> WorkerRegisterResponse:
    """Register a worker and its capabilities."""
    caps = cast("list[AgentCapability]", body.capabilities)
    deployment_id = await deployments.create(body.pool, caps)
    await deployments.register_worker(deployment_id, body.worker_id)
    for cap in body.capabilities:
        manifest.declare(
            cap.get("agent_id", ""),
            cap.get("command", ""),
            description=cap.get("description", ""),
            pool=cap.get("pool", body.pool),
            worker_id=body.worker_id,
        )
    return WorkerRegisterResponse(deployment_id=deployment_id)


@router.post(
    "/worker/heartbeat",
    dependencies=[Depends(require_api_key)],
)
async def heartbeat(
    body: HeartbeatRequest,
    deployments: Deployments,
    manifest: Manifest,
) -> dict[str, str]:
    """Update heartbeat for a worker.

    If capabilities are included, reconciles the manifest: declares
    new/updated capabilities for this worker and removes any the worker
    no longer advertises.
    """
    await deployments.heartbeat(body.worker_id)

    if body.capabilities is not None:
        caps = [
            AgentCapability(
                agent_id=c.get("agent_id", ""),
                command=c.get("command", ""),
                description=c.get("description", ""),
                pool=c.get("pool", body.pool),
            )
            for c in body.capabilities
        ]
        manifest.reconcile_worker(body.worker_id, caps)

        # Also update the deployment record's capabilities.
        await deployments.update_capabilities(body.worker_id, body.capabilities)

    return {"status": "ok"}


@router.get(
    "/tasks/claim/{pool}",
    dependencies=[Depends(require_api_key)],
)
async def claim_task(
    pool: str,
    response: Response,
    queue: Queue,
) -> dict[str, Any] | None:
    """Claim the next pending task in a pool (legacy non-blocking).

    Kept for RemoteQueue backwards compatibility. New workers should
    use ``POST /api/v1/pools/{pool}/claim`` which honours ``block_ms``
    and ``consumer_id``.
    """
    record = await queue.claim(pool, consumer_id="server", block_ms=0)
    if record is None:
        response.status_code = 204
        return None
    return dict(record)


@router.post(
    "/pools/{pool}/claim",
    dependencies=[Depends(require_api_key)],
)
async def claim_from_pool(
    pool: str,
    body: PoolClaimRequest,
    response: Response,
    queue: Queue,
) -> dict[str, Any] | None:
    """Claim one task from the pool, server-blocking up to ``block_ms``.

    The server issues ``XREADGROUP ... BLOCK block_ms`` (or the memory
    equivalent) so the worker's HTTP request waits until a task lands
    or the timeout elapses. Returns the task record on success or 204
    No Content on timeout.
    """
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
    """Fire-and-forget progress event from a remote worker.

    Rejects bodies larger than ``MAX_INLINE_PAYLOAD_BYTES`` (413). The
    server publishes to Redis Pub/Sub (or the in-memory fan-out); lost
    publishes are acceptable per ADR §progress-flow loss budget.
    """
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
    "/deployments",
    dependencies=[Depends(require_api_key)],
)
async def list_deployments(
    deployments: Deployments,
    pool: str | None = None,
) -> list[dict[str, Any]]:
    """List active deployments, optionally filtered by pool."""
    records = await deployments.get_active(pool)
    return [dict(r) for r in records]


@router.post(
    "/deployments",
    status_code=201,
    dependencies=[Depends(require_api_key)],
)
async def create_deployment(
    body: CreateDeploymentRequest,
    deployments: Deployments,
) -> dict[str, str]:
    """Create a deployment record."""
    caps = cast("list[AgentCapability]", body.capabilities)
    deployment_id = await deployments.create(body.pool, caps)
    return {"deployment_id": deployment_id}


@router.get("/health")
async def health(
    deployments: Deployments,
    queue: Queue,
    response: Response,
) -> HealthResponse:
    """Health check. No authentication required.

    On a Redis-backed queue, PING is required to return 200 — a Redis
    outage must surface as 503 here so load balancers stop routing to
    broken replicas instead of returning a false-healthy 200.
    """
    active = await deployments.get_active()
    worker_count = len(active)
    queued = getattr(queue, "pending_count", 0)
    redis_status: str | None = None
    if isinstance(queue, RedisStreamsTaskQueue):
        if await queue.ping():
            redis_status = "ok"
        else:
            redis_status = "down"
            response.status_code = 503
            return HealthResponse(
                status="degraded",
                workers=worker_count,
                queued=queued,
                redis=redis_status,
            )
    return HealthResponse(
        status="ok", workers=worker_count, queued=queued, redis=redis_status
    )
