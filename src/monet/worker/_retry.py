"""Async retry helper with exponential backoff and jitter.

A single-function utility for retrying HTTP calls that hit transient
failures. Retries on connection-level errors and configurable HTTP
status codes; non-retryable exceptions propagate immediately.

Usage::

    async def _do() -> str:
        resp = await client.post("/endpoint", json=payload)
        resp.raise_for_status()
        return str(resp.json()["id"])

    result = await retry_with_backoff(_do, max_attempts=5, logger=_log)
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

__all__ = ["retry_with_backoff"]

_DEFAULT_RETRYABLE: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    OSError,
)
_DEFAULT_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({502, 503, 504})


async def retry_with_backoff[T](
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable: tuple[type[BaseException], ...] = _DEFAULT_RETRYABLE,
    retryable_status_codes: frozenset[int] = _DEFAULT_RETRYABLE_STATUS_CODES,
    logger: logging.Logger | None = None,
) -> T:
    """Call *fn* with retries on transient failures.

    Retries on exceptions in *retryable* and on ``HTTPStatusError`` whose
    status code is in *retryable_status_codes*. Non-retryable exceptions
    propagate immediately. On the final attempt, the original exception
    is re-raised unwrapped.

    Delay between attempts follows exponential backoff with jitter:
    ``min(base_delay * 2**attempt, max_delay) * uniform(0.5, 1.0)``.

    Args:
        fn: Zero-argument async callable to retry.
        max_attempts: Maximum number of attempts (including the first).
        base_delay: Initial delay in seconds before the second attempt.
        max_delay: Upper bound on the delay between attempts.
        retryable: Exception types that trigger a retry.
        retryable_status_codes: HTTP status codes that trigger a retry.
        logger: Optional logger for WARNING-level retry messages.

    Returns:
        Whatever *fn* returns on success.

    Raises:
        The original exception from *fn* after the final attempt fails,
        or immediately for non-retryable exceptions.
    """
    for attempt in range(max_attempts):
        try:
            return await fn()
        except retryable as exc:
            if attempt == max_attempts - 1:
                raise
            delay = _compute_delay(attempt, base_delay, max_delay)
            if logger is not None:
                logger.warning(
                    "Attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt + 1,
                    max_attempts,
                    exc,
                    delay,
                )
            await asyncio.sleep(delay)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in retryable_status_codes:
                raise
            if attempt == max_attempts - 1:
                raise
            delay = _compute_delay(attempt, base_delay, max_delay)
            if logger is not None:
                logger.warning(
                    "Attempt %d/%d failed (HTTP %d), retrying in %.1fs",
                    attempt + 1,
                    max_attempts,
                    exc.response.status_code,
                    delay,
                )
            await asyncio.sleep(delay)

    # Satisfy mypy: the loop always returns or re-raises on final attempt,
    # but the type checker cannot prove exhaustiveness.
    msg = "retry_with_backoff: max_attempts must be >= 1"
    raise RuntimeError(msg)


def _compute_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    """Exponential backoff with jitter."""
    capped = min(base_delay * (2**attempt), max_delay)
    return float(capped * random.uniform(0.5, 1.0))
