"""Aegra-compatible Bearer-token auth handler for the monet server.

Aegra loads this module when ``aegra.json`` contains::

    {
      "auth": {"path": "monet.server._aegra_auth:auth"}
    }

The handler validates the ``Authorization: Bearer <token>`` header against
``MONET_API_KEY`` on every request routed through Aegra (threads, runs,
assistants, store, stateless runs) and monet's own custom routes (via
``enable_custom_route_auth: true`` in ``aegra.json``). Unset
``MONET_API_KEY`` raises — never silently allow anonymous traffic on a
server that declared this handler. Set it in the process env before
boot; :meth:`ServerConfig.validate_for_boot` already enforces this in
distributed mode.
"""

from __future__ import annotations

from langgraph_sdk import Auth

from monet.config import AuthConfig

auth = Auth()


@auth.authenticate
async def _authenticate(headers: dict[str, str]) -> dict[str, str | bool]:
    expected = AuthConfig.load().api_key
    if not expected:
        raise Auth.exceptions.HTTPException(
            status_code=500, detail="MONET_API_KEY not configured"
        )

    raw = headers.get("authorization") or headers.get("Authorization") or ""
    if not raw.lower().startswith("bearer "):
        raise Auth.exceptions.HTTPException(
            status_code=401, detail="Missing bearer token"
        )

    token = raw[7:].strip()
    if token != expected:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid API key")

    return {
        "identity": "monet",
        "display_name": "monet",
        "is_authenticated": True,
    }


__all__ = ["auth"]
