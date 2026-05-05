from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from monet.adapter._config import AdapterConfig
from monet.adapter._health import (
    HealthCascade,
    build_cascade,
    check_health,
    wait_healthy,
)


def _openai_config() -> AdapterConfig:
    return AdapterConfig(name="t", type="openai", url="http://localhost:8642")


def _http_config() -> AdapterConfig:
    from monet.adapter._config import ResponseConfig

    return AdapterConfig(
        name="t",
        type="http",
        url="http://localhost:9000/chat",
        health="/health",
        response=ResponseConfig(output="$.msg"),
    )


@pytest.mark.asyncio
@respx.mock
async def test_openai_health_endpoint_wins() -> None:
    respx.get("http://localhost:8642/health").mock(return_value=httpx.Response(200))
    cascade = build_cascade(_openai_config())
    assert await check_health(cascade) is True


@pytest.mark.asyncio
@respx.mock
async def test_openai_falls_back_to_v1_models() -> None:
    respx.get("http://localhost:8642/health").mock(return_value=httpx.Response(503))
    respx.get("http://localhost:8642/v1/models").mock(return_value=httpx.Response(200))
    cascade = build_cascade(_openai_config())
    assert await check_health(cascade) is True


@pytest.mark.asyncio
@respx.mock
async def test_openai_falls_back_to_tcp() -> None:
    respx.get("http://localhost:8642/health").mock(side_effect=httpx.ConnectError(""))
    respx.get("http://localhost:8642/v1/models").mock(
        side_effect=httpx.ConnectError("")
    )

    async def _fake_connect(host: str, port: int) -> tuple:
        writer = AsyncMock()
        writer.close = lambda: None
        writer.wait_closed = AsyncMock()
        return (AsyncMock(), writer)

    with patch("monet.adapter._health.asyncio.open_connection", new=_fake_connect):
        cascade = build_cascade(_openai_config())
        assert await check_health(cascade) is True


@pytest.mark.asyncio
async def test_wait_healthy_timeout() -> None:
    cascade = HealthCascade(checks=[AsyncMock(return_value=False)])
    with pytest.raises(TimeoutError):
        await wait_healthy(cascade, timeout=0)
