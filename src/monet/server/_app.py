"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from ._agent_routes import router as agent_router
from ._catalogue_routes import router as catalogue_router
from ._health import router as health_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="monet", version="0.1.0")
    app.include_router(health_router)
    app.include_router(agent_router, prefix="/agents")
    app.include_router(catalogue_router, prefix="/artifacts")
    return app
