"""FastAPI application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI

from ._agent_routes import router as agent_router
from ._catalogue_routes import router as catalogue_router
from ._catalogue_routes import set_catalogue_service
from ._health import router as health_router

if TYPE_CHECKING:
    from monet.catalogue._protocol import CatalogueClient


def create_app(
    catalogue_service: CatalogueClient | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        catalogue_service: Optional catalogue client. If provided,
            catalogue routes will be functional. If None, they
            return 501.
    """
    app = FastAPI(title="monet", version="0.1.0")
    app.include_router(health_router)
    app.include_router(agent_router, prefix="/agents")
    app.include_router(catalogue_router, prefix="/artifacts")

    if catalogue_service is not None:
        set_catalogue_service(catalogue_service)

    return app
