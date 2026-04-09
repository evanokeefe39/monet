"""Tests for AgentStream — subprocess transport, handlers, defaults."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from monet import AgentStream, SemanticError
from monet._catalogue import _artifact_collector
from monet._stubs import _signal_collector
from monet.catalogue import InMemoryCatalogueClient, configure_catalogue


@pytest.fixture(autouse=True)
def _catalogue() -> None:  # type: ignore[misc]
    configure_catalogue(InMemoryCatalogueClient())
    yield
    configure_catalogue(None)


def _write_emitter(tmp_path: Path, payloads: list[dict]) -> list[str]:
    """Write a tiny Python script that emits the given JSON lines."""
    script = tmp_path / "emit.py"
    body = "import json\n"
    for p in payloads:
        body += f"print(json.dumps({p!r}), flush=True)\n"
    script.write_text(body)
    return [sys.executable, str(script)]


async def test_cli_default_handlers_route_events(tmp_path: Path) -> None:
    """Default handlers wire artifact → catalogue, signal → collector,
    result → return value."""
    cmd = _write_emitter(
        tmp_path,
        [
            {
                "type": "artifact",
                "content_type": "text/markdown",
                "summary": "hello",
                "confidence": 0.8,
                "completeness": "complete",
                "content": "# Hi",
            },
            {
                "type": "signal",
                "signal_type": "low_confidence",
                "reason": "thin sources",
                "metadata": {"count": 1},
            },
            {"type": "result", "output": "done"},
        ],
    )

    artifacts: list = []
    signals: list = []
    art_token = _artifact_collector.set(artifacts)
    sig_token = _signal_collector.set(signals)
    try:
        result = await AgentStream.cli(cmd=cmd).run()
    finally:
        _artifact_collector.reset(art_token)
        _signal_collector.reset(sig_token)

    assert result == "done"
    assert len(artifacts) == 1
    assert len(signals) == 1
    assert signals[0]["type"] == "low_confidence"


async def test_cli_custom_handler_overrides_default(tmp_path: Path) -> None:
    cmd = _write_emitter(
        tmp_path,
        [{"type": "progress", "status": "x"}, {"type": "result", "output": "ok"}],
    )

    seen: list[dict] = []
    result = await AgentStream.cli(cmd=cmd).on("progress", seen.append).run()

    assert result == "ok"
    assert seen == [{"type": "progress", "status": "x"}]


async def test_cli_unknown_signal_type_raises(tmp_path: Path) -> None:
    cmd = _write_emitter(
        tmp_path,
        [{"type": "signal", "signal_type": "made_up", "reason": "x", "metadata": None}],
    )
    with pytest.raises(ValueError, match="Unknown signal_type"):
        await AgentStream.cli(cmd=cmd).run()


async def test_cli_error_event_raises_semantic_error(tmp_path: Path) -> None:
    cmd = _write_emitter(
        tmp_path,
        [{"type": "error", "error_type": "tool_unavailable", "message": "503"}],
    )
    with pytest.raises(SemanticError):
        await AgentStream.cli(cmd=cmd).run()


def test_grpc_constructor_reserved() -> None:
    with pytest.raises(NotImplementedError):
        AgentStream.grpc()


# --- _iter_http_poll ---


async def test_http_poll_timeout_after_max_polls() -> None:
    """_iter_http_poll raises TimeoutError when max_polls is exhausted."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from monet.streams import _iter_http_poll

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"type": "progress", "data": "working"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        events: list[dict] = []
        with pytest.raises(TimeoutError, match="did not produce a result event"):
            async for event in _iter_http_poll(
                "http://example.com/poll", 0.0, max_polls=3
            ):
                events.append(event)
        assert len(events) == 3
