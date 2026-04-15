"""API key authentication for the monet server.

Two dependencies:

- :func:`require_api_key` — shared bearer for client-facing and pull
  worker endpoints. Validated against ``MONET_API_KEY``.
- :func:`require_task_auth` — accepts EITHER the shared API key OR an
  HMAC bearer derived from ``HMAC_SHA256(MONET_API_KEY, task_id)``.
  Push workers (Cloud Run, Lambda, ACA) carry the per-task HMAC in
  their dispatch envelope; pull workers reuse the shared key.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from monet.config import AuthConfig

__all__ = ["require_api_key", "require_task_auth", "task_hmac"]

_security = HTTPBearer()


def task_hmac(api_key: str, task_id: str) -> str:
    """Derive the per-task HMAC bearer. Hex digest of HMAC_SHA256."""
    return hmac.new(api_key.encode(), task_id.encode(), hashlib.sha256).hexdigest()


async def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_security)],
) -> str:
    """Validate the Bearer token against the configured ``MONET_API_KEY``.

    Returns the validated API key on success. 401 on mismatch, 500 when
    the server was started without a key configured.
    """
    expected = AuthConfig.load().api_key
    if not expected:
        raise HTTPException(status_code=500, detail="MONET_API_KEY not configured")
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials


async def require_task_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_security)],
) -> str:
    """Accept either the shared ``MONET_API_KEY`` or a per-task HMAC.

    Push workers receive an HMAC bearer bound to their ``task_id`` in
    the dispatch envelope. Pull workers reuse the shared API key so the
    two paths converge on the same complete / fail / progress endpoints.

    The route must include ``{task_id}`` as a path parameter; the
    dependency reads it from ``request.path_params``. Raises 401 on
    mismatch of both, 500 when ``MONET_API_KEY`` is unset.
    """
    task_id = request.path_params.get("task_id")
    if not isinstance(task_id, str):
        raise HTTPException(
            status_code=500,
            detail="require_task_auth requires a {task_id} path parameter",
        )
    expected = AuthConfig.load().api_key
    if not expected:
        raise HTTPException(status_code=500, detail="MONET_API_KEY not configured")
    presented = credentials.credentials
    if hmac.compare_digest(presented, expected):
        return presented
    expected_hmac = task_hmac(expected, task_id)
    if hmac.compare_digest(presented, expected_hmac):
        return presented
    raise HTTPException(status_code=401, detail="Invalid token")
