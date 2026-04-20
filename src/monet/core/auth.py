"""Shared authentication primitives.

Functions here are used by both the server (token validation) and
orchestration (token minting for push dispatch). Placing them in
``core`` breaks the circular dependency between those two packages.
"""

from __future__ import annotations

import hashlib
import hmac

__all__ = ["task_hmac"]


def task_hmac(api_key: str, task_id: str) -> str:
    """Derive per-task HMAC bearer. HMAC_SHA256(api_key, task_id).hexdigest()."""
    return hmac.new(api_key.encode(), task_id.encode(), hashlib.sha256).hexdigest()
