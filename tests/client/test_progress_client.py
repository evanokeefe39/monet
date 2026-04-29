"""Tests for ProgressClient."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monet.client._core import _ClientCore
from monet.client._events import AgentProgress
from monet.client._progress import ProgressClient
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
async def test_get_progress_history_parses_events() -> None:
    core = _make_core()
    client = ProgressClient(core)

    with patch(
        "httpx.AsyncClient",
        return_value=_mock_http(
            {"events": [{"agent": "researcher", "status": "running", "command": "run"}]}
        ),
    ):
        result = await client.get_progress_history("run-1")

    assert len(result) == 1
    assert isinstance(result[0], AgentProgress)
    assert result[0].agent_id == "researcher"


@pytest.mark.asyncio
async def test_get_batch_progress_empty_input() -> None:
    core = _make_core()
    client = ProgressClient(core)
    result = await client.get_batch_progress([])
    assert result == []


@pytest.mark.asyncio
async def test_get_batch_progress_maps_run_ids() -> None:
    core = _make_core()
    client = ProgressClient(core)

    with patch(
        "httpx.AsyncClient",
        return_value=_mock_http(
            {
                "progress": {
                    "run-1": [{"agent": "writer", "status": "done", "command": "draft"}]
                }
            }
        ),
    ):
        result = await client.get_batch_progress(["run-1"])

    assert len(result) == 1
    assert result[0].run_id == "run-1"


@pytest.mark.asyncio
async def test_query_events_delegates_to_wire() -> None:
    core = _make_core()
    client = ProgressClient(core)

    mock_fn = AsyncMock(return_value=[{"event_type": "agent_started"}])
    with patch("monet.client._wire.query_progress_events", new=mock_fn):
        result = await client.query_events("run-1", after=5, limit=10)

    mock_fn.assert_awaited_once_with(
        "http://localhost:2026", "run-1", api_key=None, after=5, limit=10
    )
    assert len(result) == 1


@pytest.mark.asyncio
async def test_subscribe_events_yields_from_wire() -> None:
    core = _make_core()
    client = ProgressClient(core)

    async def fake_stream(*a: Any, **kw: Any) -> Any:
        yield {"event_type": "agent_started"}
        yield {"event_type": "run_completed"}

    with patch("monet.client._wire.stream_progress_events", new=fake_stream):
        events = [e async for e in client.subscribe_events("run-1")]

    assert len(events) == 2
