"""API key authentication for the monet server.

Two dependencies:

- :func:`require_api_key` — shared bearer for client-facing and pull
  worker endpoints. Validated against ``MONET_API_KEY``. When
  ``MONET_API_KEY`` is unset the server is in keyless dev mode and the
  dependency is a no-op (any or no ``Authorization`` header is accepted).
- :func:`require_task_auth` — accepts EITHER the shared API key OR an
  HMAC bearer derived from ``HMAC_SHA256(MONET_API_KEY, task_id)``.
  Push workers (Cloud Run, Lambda, ACA) carry the per-task HMAC in
  their dispatch envelope; pull workers reuse the shared key.
  ``MONET_API_KEY`` must be set for this dependency — task auth always
  requires a real key.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from monet.config import AuthConfig

__all__ = ["require_api_key", "require_task_auth", "task_hmac"]

# Strict bearer — used when the server key is known to be configured.
_security = HTTPBearer(auto_error=True)
# Lenient bearer — passes when Authorization is absent; used for
# endpoints that must be accessible in keyless dev mode.
_security_optional = HTTPBearer(auto_error=False)


def task_hmac(api_key: str, task_id: str) -> str:
    """Derive the per-task HMAC bearer. Hex digest of HMAC_SHA256."""
    return hmac.new(api_key.encode(), task_id.encode(), hashlib.sha256).hexdigest()


async def require_api_key(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(_security_optional)
    ],
) -> str:
    """Validate the Bearer token against the configured ``MONET_API_KEY``.

    When ``MONET_API_KEY`` is unset the server is in keyless dev mode and
    all API-key-gated endpoints are open — any (or no) ``Authorization``
    header is accepted.  This lets ``monet dev`` work without a key while
    deployed instances enforce authentication.

    Returns the validated API key (or ``""`` in keyless mode) on success.
    Raises 401 on mismatch when a key is configured.
    """
    expected = AuthConfig.load().api_key
    if not expected:
        return ""  # keyless dev mode — endpoint is open
    token = credentials.credentials if credentials is not None else ""
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token


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
