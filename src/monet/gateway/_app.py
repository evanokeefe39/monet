"""Gateway FastAPI app factory."""

from __future__ import annotations

from fastapi import FastAPI

from monet.gateway._routes import GatewayContext, mount_routes

__all__ = ["create_gateway_app"]


def create_gateway_app(ctx: GatewayContext) -> FastAPI:
    """Create the gateway FastAPI app with context injected.

    Args:
        ctx: Fully initialised GatewayContext. All route handlers close
             over this instance — no global state.

    Returns:
        FastAPI application ready to serve or mount.
    """
    app = FastAPI(
        title="Monet Data Plane Gateway",
        docs_url=None,
        redoc_url=None,
    )
    mount_routes(app, ctx)
    return app
