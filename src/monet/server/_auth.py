"""API key authentication for the monet server."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

__all__ = ["require_api_key"]

_security = HTTPBearer()


async def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_security)],
) -> str:
    """FastAPI dependency that validates the Bearer token against MONET_API_KEY.

    Returns the validated API key string on success.
    Raises HTTPException 401 if the key is missing or invalid.
    Raises HTTPException 500 if MONET_API_KEY is not configured.
    """
    expected = os.environ.get("MONET_API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="MONET_API_KEY not configured")
    if credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials
