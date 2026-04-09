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
    *,
    timeout: float = 10.0,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Return an async handler that POSTs each event dict to ``url`` as JSON.

    Catches transport and HTTP errors so a failing webhook does not crash
    the stream handler chain. Failures are logged at warning level.
    """

    async def handler(data: dict[str, Any]) -> None:
        import logging

        import httpx

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                await client.post(url, json=data)
        except httpx.HTTPError as exc:
            logging.getLogger("monet.handlers").warning(
                "webhook POST to %s failed: %s", url, exc
            )

    return handler


def log_handler(
    logger: logging.Logger, level: str = "info"
) -> Callable[[dict[str, Any]], None]:
    """Return a sync handler that logs each event at ``level``."""

    def handler(data: dict[str, Any]) -> None:
        getattr(logger, level)("agent event: %s", data)

    return handler


__all__ = ["log_handler", "webhook_handler"]
