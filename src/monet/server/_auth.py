"""API key authentication for the monet server."""

from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from monet.config import AuthConfig

__all__ = ["require_api_key"]

_security = HTTPBearer()


async def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_security)],
) -> str:
    """Validate the Bearer token against the configured ``MONET_API_KEY``.

    Returns the validated API key string on success. Raises
    :exc:`HTTPException` 401 on mismatch, 500 when the server was started
    without a key configured.

    In distributed mode :meth:`ServerConfig.validate_for_boot` prevents
    the 500 case by failing the process start when the env var is unset
    — so reaching this 500 branch indicates a server mounted an
    authenticated route without having run the boot validation (e.g. a
    user-built FastAPI app that bypassed ``bootstrap()``).
    """
    expected = AuthConfig.load().api_key
    if not expected:
        raise HTTPException(status_code=500, detail="MONET_API_KEY not configured")
    if credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials
