"""Unified router for all API modules."""

from fastapi import APIRouter

from monet.server.routes import (
    _artifacts,
    _invocations,
    _ops,
    _tasks_control,
    _tasks_data,
    _threads,
    _workers,
)

router = APIRouter(prefix="/api/v1")

router.include_router(_workers.router)
router.include_router(_tasks_control.router)
router.include_router(_tasks_data.router)
router.include_router(_artifacts.router)
router.include_router(_threads.router)
router.include_router(_ops.router)
router.include_router(_invocations.router)

__all__ = ["router"]
