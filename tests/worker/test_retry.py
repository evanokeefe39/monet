"""Tests for the retry_with_backoff helper."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from monet.worker._retry import retry_with_backoff


def _make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build an HTTPStatusError with the given response status."""
    request = httpx.Request("POST", "http://test/endpoint")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


async def test_succeeds_first_attempt() -> None:
    """No retry when the first call succeeds."""
    fn = AsyncMock(return_value="ok")
    with patch("asyncio.sleep", new=AsyncMock()):
        result = await retry_with_backoff(fn, max_attempts=5)
    assert result == "ok"
    assert fn.call_count == 1


async def test_succeeds_after_transient_failures() -> None:
    """Retries on ConnectError then returns success on third attempt."""
    fn = AsyncMock(
        side_effect=[
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            "ok",
        ]
    )
    with patch("asyncio.sleep", new=AsyncMock()):
        result = await retry_with_backoff(fn, max_attempts=5)
    assert result == "ok"
    assert fn.call_count == 3


async def test_exhausts_all_attempts() -> None:
    """Re-raises the original exception after max_attempts."""
    fn = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with (
        patch("asyncio.sleep", new=AsyncMock()),
        pytest.raises(httpx.ConnectError),
    ):
        await retry_with_backoff(fn, max_attempts=3)
    assert fn.call_count == 3


async def test_non_retryable_http_status() -> None:
    """401 is not retried; re-raises immediately."""
    err = _make_http_status_error(401)
    fn = AsyncMock(side_effect=err)
    with (
        patch("asyncio.sleep", new=AsyncMock()),
        pytest.raises(httpx.HTTPStatusError) as exc_info,
    ):
        await retry_with_backoff(fn, max_attempts=5)
    assert exc_info.value.response.status_code == 401
    assert fn.call_count == 1


async def test_retryable_http_status() -> None:
    """503 triggers retry; succeeds on second attempt."""
    fn = AsyncMock(side_effect=[_make_http_status_error(503), "ok"])
    with patch("asyncio.sleep", new=AsyncMock()):
        result = await retry_with_backoff(fn, max_attempts=5)
    assert result == "ok"
    assert fn.call_count == 2


async def test_non_retryable_exception() -> None:
    """ValueError is not in retryable tuple; re-raises immediately."""
    fn = AsyncMock(side_effect=ValueError("boom"))
    with (
        patch("asyncio.sleep", new=AsyncMock()),
        pytest.raises(ValueError, match="boom"),
    ):
        await retry_with_backoff(fn, max_attempts=5)
    assert fn.call_count == 1


async def test_delay_bounds() -> None:
    """Delays are within expected exponential bounds with jitter."""
    fn = AsyncMock(
        side_effect=[
            httpx.ConnectError("x"),
            httpx.ConnectError("x"),
            httpx.ConnectError("x"),
            "ok",
        ]
    )
    sleep_mock = AsyncMock()
    with patch("asyncio.sleep", new=sleep_mock):
        await retry_with_backoff(fn, max_attempts=5, base_delay=1.0, max_delay=30.0)

    # Three sleeps between four attempts. Each delay: jitter * min(base*2^n, max).
    # Attempt 0 cap = 1.0, attempt 1 cap = 2.0, attempt 2 cap = 4.0.
    # Jitter multiplier is uniform(0.5, 1.0), so delay ∈ [0.5 * cap, cap].
    assert sleep_mock.call_count == 3
    delays = [call.args[0] for call in sleep_mock.call_args_list]
    assert 0.5 <= delays[0] <= 1.0
    assert 1.0 <= delays[1] <= 2.0
    assert 2.0 <= delays[2] <= 4.0


async def test_delay_respects_max() -> None:
    """Delay is capped at max_delay even when exponential grows past it."""
    fn = AsyncMock(side_effect=[httpx.ConnectError("x")] * 6 + ["ok"])
    sleep_mock = AsyncMock()
    with patch("asyncio.sleep", new=sleep_mock):
        await retry_with_backoff(fn, max_attempts=7, base_delay=1.0, max_delay=5.0)
    # By attempt 4, base*2^4 = 16 > max_delay=5, so cap is 5.
    # Jitter bounds: [2.5, 5.0].
    last_delay = sleep_mock.call_args_list[-1].args[0]
    assert 2.5 <= last_delay <= 5.0


async def test_logs_on_retry(caplog: pytest.LogCaptureFixture) -> None:
    """Retry attempts are logged at WARNING level when a logger is provided."""
    logger = logging.getLogger("test.retry")
    fn = AsyncMock(side_effect=[httpx.ConnectError("refused"), "ok"])
    with (
        patch("asyncio.sleep", new=AsyncMock()),
        caplog.at_level(logging.WARNING, logger="test.retry"),
    ):
        await retry_with_backoff(fn, max_attempts=5, logger=logger)
    assert any("Attempt 1/5 failed" in rec.message for rec in caplog.records)


async def test_logs_on_retryable_status(caplog: pytest.LogCaptureFixture) -> None:
    """HTTP status retries are logged with the status code."""
    logger = logging.getLogger("test.retry.status")
    fn = AsyncMock(side_effect=[_make_http_status_error(503), "ok"])
    with (
        patch("asyncio.sleep", new=AsyncMock()),
        caplog.at_level(logging.WARNING, logger="test.retry.status"),
    ):
        await retry_with_backoff(fn, max_attempts=5, logger=logger)
    assert any("HTTP 503" in rec.message for rec in caplog.records)


# ── heartbeat_with_tracking tests ───────────────────────────────────────


async def test_heartbeat_tracking_counter_increments_on_transient() -> None:
    """Consecutive transient failures increment the counter."""
    from monet.worker import WorkerClient

    client = WorkerClient("http://test", "key")
    client.heartbeat = AsyncMock(  # type: ignore[method-assign]
        side_effect=httpx.ConnectError("down")
    )

    await client.heartbeat_with_tracking("w1", "local")
    assert client._consecutive_heartbeat_failures == 1

    await client.heartbeat_with_tracking("w1", "local")
    assert client._consecutive_heartbeat_failures == 2

    await client._client.aclose()


async def test_heartbeat_tracking_counter_resets_on_success() -> None:
    """Counter resets to zero after a successful heartbeat."""
    from monet.worker import WorkerClient

    client = WorkerClient("http://test", "key")
    # First call raises, second call returns None (success).
    client.heartbeat = AsyncMock(  # type: ignore[method-assign]
        side_effect=[httpx.ConnectError("down"), None]
    )

    await client.heartbeat_with_tracking("w1", "local")
    assert client._consecutive_heartbeat_failures == 1

    await client.heartbeat_with_tracking("w1", "local")
    assert client._consecutive_heartbeat_failures == 0

    await client._client.aclose()


async def test_heartbeat_tracking_escalates_log_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log level escalates from WARNING to ERROR at 3 consecutive failures."""
    from monet.worker import WorkerClient

    client = WorkerClient("http://test", "key")
    client.heartbeat = AsyncMock(  # type: ignore[method-assign]
        side_effect=httpx.ConnectError("down")
    )

    with caplog.at_level(logging.WARNING, logger="monet.worker._client"):
        for _ in range(3):
            await client.heartbeat_with_tracking("w1", "local")

    levels = [rec.levelno for rec in caplog.records]
    assert logging.WARNING in levels
    assert logging.ERROR in levels

    await client._client.aclose()


async def test_heartbeat_tracking_propagates_4xx() -> None:
    """4xx status errors (e.g. 401 auth failure) propagate; counter not touched."""
    from monet.worker import WorkerClient

    client = WorkerClient("http://test", "key")
    client.heartbeat = AsyncMock(  # type: ignore[method-assign]
        side_effect=_make_http_status_error(401)
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.heartbeat_with_tracking("w1", "local")
    assert exc_info.value.response.status_code == 401
    assert client._consecutive_heartbeat_failures == 0

    await client._client.aclose()


async def test_heartbeat_tracking_swallows_5xx() -> None:
    """5xx status errors are treated as transient and swallowed."""
    from monet.worker import WorkerClient

    client = WorkerClient("http://test", "key")
    client.heartbeat = AsyncMock(  # type: ignore[method-assign]
        side_effect=_make_http_status_error(503)
    )

    await client.heartbeat_with_tracking("w1", "local")  # should not raise
    assert client._consecutive_heartbeat_failures == 1

    await client._client.aclose()
