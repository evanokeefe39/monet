"""Tests for API key authentication middleware."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from monet.server._auth import require_api_key

TEST_API_KEY = "test-secret-key-12345"


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with the auth dependency."""
    app = FastAPI()

    @app.get("/protected")
    async def protected(api_key: str = Depends(require_api_key)) -> dict[str, str]:
        return {"key": api_key}

    return app


@pytest.fixture
def app() -> FastAPI:
    return _make_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_valid_api_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONET_API_KEY", TEST_API_KEY)
    resp = await client.get(
        "/protected",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"key": TEST_API_KEY}


@pytest.mark.asyncio
async def test_invalid_api_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MONET_API_KEY", TEST_API_KEY)
    resp = await client.get(
        "/protected",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API key"


@pytest.mark.asyncio
async def test_keyless_dev_mode(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset MONET_API_KEY → server is open; endpoint returns 200 with empty key."""
    monkeypatch.delenv("MONET_API_KEY", raising=False)
    resp = await client.get(
        "/protected",
        headers={"Authorization": "Bearer any-key"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"key": ""}


@pytest.mark.asyncio
async def test_no_auth_header_keyless(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Authorization header + unset key → keyless mode, endpoint is open."""
    monkeypatch.delenv("MONET_API_KEY", raising=False)
    resp = await client.get("/protected")
    assert resp.status_code == 200
    assert resp.json() == {"key": ""}


@pytest.mark.asyncio
async def test_no_auth_header_keyed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Authorization header with key configured → 401."""
    monkeypatch.setenv("MONET_API_KEY", TEST_API_KEY)
    resp = await client.get("/protected")
    assert resp.status_code == 401
