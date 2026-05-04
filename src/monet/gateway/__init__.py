"""Monet data plane gateway.

Stateless HTTP service for agent-to-monet communication: artifact I/O,
progress events, and signal accumulation.
"""

from monet.gateway._app import create_gateway_app
from monet.gateway._auth import DEV_SIGNING_KEY, mint_task_token, validate_token
from monet.gateway._routes import GatewayContext

__all__ = [
    "DEV_SIGNING_KEY",
    "GatewayContext",
    "create_gateway_app",
    "mint_task_token",
    "validate_token",
]
