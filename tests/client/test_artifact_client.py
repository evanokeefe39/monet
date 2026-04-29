"""Tests for ArtifactClient."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monet.client._artifacts import ArtifactClient, ArtifactSummary
from monet.client._core import _ClientCore
from monet.client._run_state import _RunStore


def _make_core(**overrides: Any) -> _ClientCore:
    defaults: dict[str, Any] = {
        "url": "http://localhost:2026",
        "api_key": None,
        "data_url": "http://localhost:2026",
        "client": MagicMock(),
        "store": _RunStore(),
        "entrypoints": {},
        "graph_roles": {},
    }
    defaults.update(overrides)
    return _ClientCore(**defaults)


def _mock_http(json_return: Any) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_return
    mock_resp.raise_for_status = MagicMock()
    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)
    return mock_http


@pytest.mark.asyncio
async def test_list_artifacts_maps_fields() -> None:
    core = _make_core()
    client = ArtifactClient(core)

    with patch(
        "httpx.AsyncClient",
        return_value=_mock_http(
            {
                "artifacts": [
                    {
                        "artifact_id": "art-1",
                        "summary": "A doc",
                        "content_type": "text/markdown",
                        "agent_id": "writer",
                        "key": "draft",
                    }
                ]
            }
        ),
    ):
        result = await client.list_artifacts(thread_id="t-1")

    assert len(result) == 1
    a = result[0]
    assert isinstance(a, ArtifactSummary)
    assert a.artifact_id == "art-1"
    assert a.kind == "text/markdown"
    assert a.key == "draft"


@pytest.mark.asyncio
async def test_list_artifacts_empty_response() -> None:
    core = _make_core()
    client = ArtifactClient(core)

    with patch("httpx.AsyncClient", return_value=_mock_http({"artifacts": []})):
        result = await client.list_artifacts(thread_id="t-1")

    assert result == []


@pytest.mark.asyncio
async def test_count_artifacts_per_thread_empty_input() -> None:
    core = _make_core()
    client = ArtifactClient(core)
    result = await client.count_artifacts_per_thread([])
    assert result == {}


@pytest.mark.asyncio
async def test_count_artifacts_per_thread_normal() -> None:
    core = _make_core()
    client = ArtifactClient(core)

    with patch(
        "httpx.AsyncClient",
        return_value=_mock_http({"t-1": 3, "t-2": 0}),
    ):
        result = await client.count_artifacts_per_thread(["t-1", "t-2"])

    assert result == {"t-1": 3, "t-2": 0}
