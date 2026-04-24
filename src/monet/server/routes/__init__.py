"""Unified router for all API modules."""

from fastapi import APIRouter

from monet.server.routes import (
    _artifacts,
    _invocations,
    _ops,
    _tasks,
    _threads,
    _workers,
)

router = APIRouter(prefix="/api/v1")

# Include sub-routers.
# Note: prefixes are handled in the sub-modules or here if needed.
# Many existing routes are already absolute from /api/v1.
router.include_router(_workers.router)
router.include_router(_tasks.router)
router.include_router(_artifacts.router)
router.include_router(_threads.router)
router.include_router(_ops.router)
router.include_router(_invocations.router)

__all__ = ["router"]
