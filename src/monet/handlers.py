"""Handler factories for AgentStream.on().

Composable callables for common stream-handling needs. Each factory
returns a handler with signature ``(event: dict) -> None`` (sync or async).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable


def webhook_handler(
    url: str,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Return an async handler that POSTs each event dict to ``url`` as JSON."""

    async def handler(data: dict[str, Any]) -> None:
        import httpx

        async with httpx.AsyncClient() as client:
            await client.post(url, json=data)

    return handler


def log_handler(
    logger: logging.Logger, level: str = "info"
) -> Callable[[dict[str, Any]], None]:
    """Return a sync handler that logs each event at ``level``."""

    def handler(data: dict[str, Any]) -> None:
        getattr(logger, level)("agent event: %s", data)

    return handler


__all__ = ["log_handler", "webhook_handler"]
