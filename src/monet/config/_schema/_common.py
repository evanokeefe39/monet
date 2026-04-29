"""Shared constants and helpers used by all schema modules."""

from __future__ import annotations

from typing import Literal

from ..._ports import STANDARD_DEV_PORT, STANDARD_LANGFUSE_PORT

QueueBackend = Literal["memory", "redis"]
_QUEUE_BACKENDS: tuple[QueueBackend, ...] = ("memory", "redis")

_SECRET = "set"
_UNSET = "unset"

_DEFAULT_SERVER_URL = f"http://localhost:{STANDARD_DEV_PORT}"
_DEFAULT_LANGFUSE_HOST = f"http://localhost:{STANDARD_LANGFUSE_PORT}"


def _redact(value: str | None) -> str:
    return _SECRET if value else _UNSET
