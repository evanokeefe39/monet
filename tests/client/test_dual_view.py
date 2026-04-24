"""Tests for Phase 2 dual-view client: control vs data plane URL routing."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from monet.client import MonetClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    url: str = "http://control:2026", data_url: str | None = None
) -> MonetClient:
    """Create a MonetClient with a mocked LangGraph SDK client."""
    with (
        patch("monet.client._wire.make_client"),
        patch("monet.client.__init__.make_client"),
        patch("monet.config.load_entrypoints", return_value=[]),
        patch("monet.config.load_graph_roles", return_value={}),
        patch("monet.client.__init__.ChatClient"),
    ):
        return MonetClient(url, data_plane_url=data_url)


def _event(event_id: int, event_type: str = "status") -> dict[str, Any]:
    return {
        "event_id": event_id,
        "run_id": "run-1",
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": event_type,
        "timestamp_ms": 1000,
    }


# ---------------------------------------------------------------------------
# ClientConfig data_plane_url resolution
# ---------------------------------------------------------------------------


def test_data_url_explicit_overrides_config() -> None:
    client = _make_client("http://control:2026", data_url="http://data:9000")
    assert client._data_url == "http://data:9000"
    assert client._url == "http://control:2026"


def test_data_url_falls_back_to_control_url() -> None:
    client = _make_client("http://control:2026", data_url=None)
    assert client._data_url == "http://control:2026"


def test_data_url_loaded_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONET_DATA_PLANE_URL", "http://data-env:9000")
    client = _make_client("http://control:2026")
    assert client._data_url == "http://data-env:9000"


# ---------------------------------------------------------------------------
# query_events uses data_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_events_uses_data_url() -> None:
    client = _make_client("http://control:2026", data_url="http://data:9000")
    events = [_event(1), _event(2)]

    captured: dict[str, Any] = {}

    async def fake_query(
        url: str, run_id: str, *, api_key: Any, after: int, limit: int
    ) -> list[Any]:
        captured["url"] = url
        captured["after"] = after
        captured["limit"] = limit
        return events

    with patch("monet.client._wire.query_progress_events", new=fake_query):
        result = await client.query_events("run-1", after=5, limit=50)

    assert captured["url"] == "http://data:9000"
    assert captured["after"] == 5
    assert captured["limit"] == 50
    assert result == events


@pytest.mark.asyncio
async def test_query_events_uses_control_url_when_no_data_plane() -> None:
    client = _make_client("http://control:2026")
    captured: dict[str, Any] = {}

    async def fake_query(
        url: str, run_id: str, *, api_key: Any, after: int, limit: int
    ) -> list[Any]:
        captured["url"] = url
        return []

    with patch("monet.client._wire.query_progress_events", new=fake_query):
        await client.query_events("run-1")

    assert captured["url"] == "http://control:2026"


# ---------------------------------------------------------------------------
# subscribe_events uses data_url and tracks cursor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_events_uses_data_url() -> None:
    client = _make_client("http://control:2026", data_url="http://data:9000")
    captured_urls: list[str] = []
    terminal = _event(3, "run_completed")
    calls = [[_event(1), _event(2)], [terminal]]
    call_idx = 0

    async def fake_stream(
        url: str, run_id: str, *, api_key: Any, after: int, poll_interval: float = 0.5
    ) -> Any:
        nonlocal call_idx
        captured_urls.append(url)
        for batch in calls:
            for ev in batch:
                yield ev

    with patch("monet.client._wire.stream_progress_events", new=fake_stream):
        collected = []
        async for ev in client.subscribe_events("run-1"):
            collected.append(ev)

    assert all(u == "http://data:9000" for u in captured_urls)
    assert len(collected) == 3


@pytest.mark.asyncio
async def test_subscribe_events_reconnect_passes_cursor() -> None:
    """Wire-level: stream_progress_events tracks after= across batches."""

    from monet.client._wire import stream_progress_events

    responses = [
        [_event(1), _event(3)],
        [_event(5, "run_completed")],
    ]
    poll_count = 0
    captured_afters: list[int] = []

    async def fake_query(
        url: str, run_id: str, *, api_key: Any, after: int, limit: int
    ) -> list[Any]:
        nonlocal poll_count
        captured_afters.append(after)
        if poll_count < len(responses):
            batch = responses[poll_count]
            poll_count += 1
            return batch
        return []

    events: list[Any] = []
    with patch("monet.client._wire.query_progress_events", new=fake_query):
        async for ev in stream_progress_events("http://data:9000", "run-1", after=0):
            events.append(ev)

    assert captured_afters[0] == 0
    assert captured_afters[1] == 3  # cursor advanced to max(event_id) from first batch
    assert len(events) == 3
