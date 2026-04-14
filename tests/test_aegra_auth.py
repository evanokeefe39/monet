"""Tests for the Aegra-compatible Bearer-token auth handler."""

from __future__ import annotations

from typing import Any, cast

import pytest
from langgraph_sdk import Auth

from monet.server._aegra_auth import auth

TEST_KEY = "test-aegra-key-abc123"


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONET_API_KEY", TEST_KEY)


async def _call_handler(headers: dict[str, str]) -> dict[str, Any]:
    handler = auth._authenticate_handler
    assert handler is not None
    return cast(dict[str, Any], await handler(headers))


@pytest.mark.asyncio
async def test_valid_bearer_returns_identity() -> None:
    user = await _call_handler({"authorization": f"Bearer {TEST_KEY}"})
    assert user["identity"] == "monet"
    assert user["is_authenticated"] is True


@pytest.mark.asyncio
async def test_case_insensitive_header_name() -> None:
    user = await _call_handler({"Authorization": f"Bearer {TEST_KEY}"})
    assert user["identity"] == "monet"


@pytest.mark.asyncio
async def test_wrong_token_rejects() -> None:
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await _call_handler({"authorization": "Bearer wrong"})
    assert exc.value.status_code == 401
    assert "Invalid API key" in exc.value.detail


@pytest.mark.asyncio
async def test_missing_bearer_prefix_rejects() -> None:
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await _call_handler({"authorization": TEST_KEY})
    assert exc.value.status_code == 401
    assert "Missing bearer token" in exc.value.detail


@pytest.mark.asyncio
async def test_missing_header_rejects() -> None:
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await _call_handler({})
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unset_api_key_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONET_API_KEY", raising=False)
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await _call_handler({"authorization": "Bearer anything"})
    assert exc.value.status_code == 500
    assert "MONET_API_KEY not configured" in exc.value.detail
