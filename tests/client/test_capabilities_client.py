"""Tests for CapabilitiesClient."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monet.client._capabilities import CapabilitiesClient
from monet.client._core import _ClientCore
from monet.client._run_state import _RunStore


def _make_core(**overrides: Any) -> _ClientCore:
    defaults: dict[str, Any] = {
        "url": "http://localhost:2026",
        "api_key": "key-1",
        "data_url": "http://localhost:2026",
        "client": MagicMock(),
        "store": _RunStore(),
        "entrypoints": {},
        "graph_roles": {},
    }
    defaults.update(overrides)
    return _ClientCore(**defaults)


@pytest.mark.asyncio
async def test_list_capabilities_parses_list() -> None:
    core = _make_core()
    client = CapabilitiesClient(core)

    mock_resp = MagicMock()
    mock_resp.json.return_value = [
        {"agent_id": "researcher", "command": "run"},
        {"agent_id": "writer", "command": "draft"},
    ]
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        caps = await client.list_capabilities()

    assert len(caps) == 2
    assert caps[0]["agent_id"] == "researcher"


@pytest.mark.asyncio
async def test_list_capabilities_returns_empty_on_non_list() -> None:
    core = _make_core()
    client = CapabilitiesClient(core)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"error": "not a list"}
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        caps = await client.list_capabilities()

    assert caps == []


@pytest.mark.asyncio
async def test_slash_commands_deduplicates() -> None:
    core = _make_core()
    client = CapabilitiesClient(core)
    client.list_capabilities = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"agent_id": "a", "command": "run"},
            {"agent_id": "a", "command": "run"},
        ]
    )

    with patch(
        "monet.server._capabilities.RESERVED_SLASH",
        ["/plan"],
    ):
        cmds = await client.slash_commands()

    assert cmds.count("/a:run") == 1
    assert "/plan" in cmds


@pytest.mark.asyncio
async def test_invoke_agent_sends_correct_payload() -> None:
    core = _make_core()
    client = CapabilitiesClient(core)

    posted: list[Any] = []

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"result": "ok"}
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    async def fake_post(url: str, json: Any, headers: Any) -> Any:
        posted.append(json)
        return mock_resp

    mock_http.post = fake_post

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await client.invoke_agent(
            "researcher", "run", task="find stuff", skills=["web"]
        )

    assert result == {"result": "ok"}
    assert posted[0]["task"] == "find stuff"
    assert posted[0]["skills"] == ["web"]
