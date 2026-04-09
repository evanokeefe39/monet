"""Tests for handler factories."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


async def test_webhook_handler_does_not_raise_on_http_error() -> None:
    """Webhook handler catches HTTPError so a failing endpoint doesn't
    crash the stream handler chain."""
    import httpx

    from monet.handlers import webhook_handler

    handler = webhook_handler("http://bad-host.invalid/hook", timeout=1.0)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        # Should not raise
        await handler({"type": "progress", "data": "test"})


async def test_webhook_handler_posts_json_on_success() -> None:
    """Webhook handler POSTs event data as JSON."""
    from monet.handlers import webhook_handler

    handler = webhook_handler("http://example.com/hook")

    mock_response = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await handler({"type": "progress", "value": 42})

    mock_client.post.assert_awaited_once_with(
        "http://example.com/hook", json={"type": "progress", "value": 42}
    )
