"""Canonical port numbers and related constants shared across monet.

Examples and `monet dev` all bind to the same local ports. Only one
example stack can run at a time — ``monet dev`` auto-tears-down the
previous stack on entry (see ``src/monet/cli/_dev.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# ── Standard local ports ────────────────────────────────────────────

STANDARD_POSTGRES_PORT = 5432
STANDARD_REDIS_PORT = 6379
STANDARD_DEV_PORT = 2026
STANDARD_LANGFUSE_PORT = 3000


# ── Wire limits ─────────────────────────────────────────────────────

# Upper bound on inline payloads (serialized TaskRecord, progress events,
# AgentResult) across the queue and HTTP boundaries. 950 KiB chosen to
# stay under Upstash's 1 MiB per-entry limit with headroom. Larger
# payloads must reference an ``ArtifactPointer`` instead of inlining.
MAX_INLINE_PAYLOAD_BYTES: Final[int] = 950_000


# ── monet state directory ───────────────────────────────────────────


def state_dir() -> Path:
    """Return the per-user monet state directory, creating it if needed.

    Used by ``monet dev`` to remember the most recently started example
    compose file so it can be torn down on the next invocation.
    """
    d = Path.home() / ".monet"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_file() -> Path:
    """Path to the shared ``state.json`` inside :func:`state_dir`."""
    return state_dir() / "state.json"


__all__ = [
    "MAX_INLINE_PAYLOAD_BYTES",
    "STANDARD_DEV_PORT",
    "STANDARD_LANGFUSE_PORT",
    "STANDARD_POSTGRES_PORT",
    "STANDARD_REDIS_PORT",
    "state_dir",
    "state_file",
]
