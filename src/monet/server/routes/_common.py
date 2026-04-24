"""Shared dependencies and schemas for API routes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from opentelemetry import context as _ot_context
from opentelemetry import propagate as _propagate
from pydantic import BaseModel

from monet.queue import TaskQueue
from monet.server._capabilities import CapabilityIndex
from monet.server._deployment import DeploymentStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = logging.getLogger("monet.server.routes")

#: Max characters of a node's ``task`` rendered inside a DAG box.
_DAG_TASK_CHAR_BUDGET = 160

#: Hard cap on artifacts returned by ``GET /api/v1/artifacts``.
ARTIFACT_LIST_MAX = 500


# -- Dependency injection helpers ------------------------------------------


def get_queue(request: Request) -> TaskQueue:
    """Retrieve the task queue from application state."""
    return request.app.state.queue  # type: ignore[no-any-return]


def get_deployments(request: Request) -> DeploymentStore:
    """Retrieve the deployment store from application state."""
    return request.app.state.deployments  # type: ignore[no-any-return]


def get_capability_index(request: Request) -> CapabilityIndex:
    """Retrieve the capability index from application state."""
    return request.app.state.capability_index  # type: ignore[no-any-return]


async def attach_trace_context(request: Request) -> AsyncIterator[None]:
    """Extract W3C traceparent from incoming headers and attach OTel context."""
    carrier = dict(request.headers)
    ctx = _propagate.extract(carrier)
    token = _ot_context.attach(ctx)
    try:
        yield
    finally:
        _ot_context.detach(token)


# Type aliases for annotated dependencies
Queue = Annotated[TaskQueue, Depends(get_queue)]
Deployments = Annotated[DeploymentStore, Depends(get_deployments)]
CapIndex = Annotated[CapabilityIndex, Depends(get_capability_index)]


# -- Common Schemas ---------------------------------------------------------


class HealthResponse(BaseModel):
    """Response for ``GET /api/v1/health``."""

    status: str
    workers: int
    queued: int
    redis: str | None = None
    version: str = ""
    queue_backend: str = ""
    uptime_seconds: float = 0.0


def monet_version() -> str:
    """Retrieve the version of the monet package."""
    try:
        from importlib.metadata import version

        return version("monet")
    except Exception:
        return ""
