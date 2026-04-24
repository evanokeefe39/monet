"""Worker and capability discovery routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from monet.server._auth import require_api_key
from monet.server._capabilities import Capability  # noqa: TC001
from monet.server.routes._common import CapIndex, Deployments  # noqa: TC001

_log = logging.getLogger("monet.server.routes.workers")

router = APIRouter()


class WorkerHeartbeatBody(BaseModel):
    """Body for unified ``POST /api/v1/workers/{worker_id}/heartbeat``."""

    pool: str
    capabilities: list[Capability]


@router.post(
    "/workers/{worker_id}/heartbeat",
    dependencies=[Depends(require_api_key)],
)
async def worker_heartbeat(
    worker_id: str,
    body: WorkerHeartbeatBody,
    deployments: Deployments,
    cap_index: CapIndex,
) -> dict[str, object]:
    """Unified registration + liveness ping for a worker."""
    cap_index.upsert_worker(worker_id, body.pool, body.capabilities)
    cap_dicts = [
        {
            "agent_id": c.agent_id,
            "command": c.command,
            "description": c.description,
            "pool": c.pool,
        }
        for c in body.capabilities
    ]

    is_new = not await deployments.worker_exists(worker_id)
    if is_new:
        deployment_id = await deployments.create(body.pool, cap_dicts)
        await deployments.register_worker(deployment_id, worker_id)
    else:
        await deployments.heartbeat(worker_id)
        await deployments.update_capabilities(worker_id, cap_dicts)

    _log.info(
        "worker.heartbeat worker=%s pool=%s caps=%d new=%s",
        worker_id,
        body.pool,
        len(body.capabilities),
        is_new,
    )
    return {
        "worker_id": worker_id,
        "known_capabilities": len(body.capabilities),
        "registered": is_new,
    }


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


@router.get("/agents", dependencies=[Depends(require_api_key)])
async def list_agents(cap_index: CapIndex) -> list[dict[str, Any]]:
    """List every capability advertised by a heartbeating worker."""
    return cap_index.capabilities()
