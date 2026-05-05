"""ZeroClaw ACP plugin for monet adapter SDK.

Implements the stdio plugin contract:
    run_task(rpc, message) -> str
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def run_task(rpc: Callable[[str, dict[str, Any]], dict[str, Any]], message: str) -> str:
    sess = rpc("session/new", {})
    session_id = str(sess.get("sessionId", ""))
    result = rpc("session/prompt", {"sessionId": session_id, "prompt": message})
    with contextlib.suppress(Exception):
        rpc("session/stop", {"sessionId": session_id})
    for key in ("content", "message", "text", "_streamed"):
        val = result.get(key)
        if isinstance(val, str) and val:
            return val
    return str(result)
