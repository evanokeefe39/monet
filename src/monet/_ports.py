"""Canonical port numbers and related constants shared across monet.

Examples and `monet dev` all bind to the same local ports. Only one
example stack can run at a time — ``monet dev`` auto-tears-down the
previous stack on entry (see ``src/monet/cli/_dev.py``).
"""

from __future__ import annotations

from pathlib import Path

# ── Standard local ports ────────────────────────────────────────────

STANDARD_POSTGRES_PORT = 5432
STANDARD_REDIS_PORT = 6379
STANDARD_DEV_PORT = 2026
STANDARD_LANGFUSE_PORT = 3000


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
    "STANDARD_DEV_PORT",
    "STANDARD_LANGFUSE_PORT",
    "STANDARD_POSTGRES_PORT",
    "STANDARD_REDIS_PORT",
    "state_dir",
    "state_file",
]
