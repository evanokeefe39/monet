"""REST API routes for the monet orchestration server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from monet.server._auth import require_api_key
from monet.types import AgentResult, ArtifactPointer, Signal

if TYPE_CHECKING:
    from monet.core.manifest import AgentManifest
    from monet.queue import TaskQueue
    from monet.server._deployment import DeploymentStore

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
Queue = Annotated[Any, Depends(get_queue)]
Deployments = Annotated[Any, Depends(get_deployments)]
Manifest = Annotated[Any, Depends(get_manifest)]


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


class HealthResponse(BaseModel):
    """Response for ``GET /api/v1/health``."""

    status: str
    workers: int
    queued: int


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
    deployment_id = await deployments.create(body.pool, body.capabilities)
    await deployments.register_worker(deployment_id, body.worker_id)
    for cap in body.capabilities:
        manifest.declare(
            cap.get("agent_id", ""),
            cap.get("command", ""),
            description=cap.get("description", ""),
            pool=body.pool,
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
        from monet.core.manifest import AgentCapability

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
    """Claim the next pending task in a pool.

    Returns the task record on success or 204 No Content when the pool
    is empty.
    """
    record = await queue.claim(pool)
    if record is None:
        response.status_code = 204
        return None
    return dict(record)


@router.post(
    "/tasks/{task_id}/complete",
    dependencies=[Depends(require_api_key)],
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
        artifacts=tuple(
            ArtifactPointer(
                artifact_id=a.get("artifact_id", ""),
                url=a.get("url", ""),
            )
            for a in body.artifacts
        ),
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
    dependencies=[Depends(require_api_key)],
)
async def fail_task(
    task_id: str,
    body: TaskFailRequest,
    queue: Queue,
) -> dict[str, str]:
    """Post a failure for a claimed task."""
    await queue.fail(task_id, body.error)
    return {"status": "ok"}


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
    deployment_id = await deployments.create(body.pool, body.capabilities)
    return {"deployment_id": deployment_id}


@router.get("/health")
async def health(
    deployments: Deployments,
    queue: Queue,
) -> HealthResponse:
    """Health check endpoint. No authentication required."""
    active = await deployments.get_active()
    worker_count = len(active)
    queued = getattr(queue, "pending_count", 0)
    return HealthResponse(status="ok", workers=worker_count, queued=queued)
