"""Tests for RunClient."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monet.client._core import _ClientCore
from monet.client._errors import (
    AlreadyResolved,
    GraphNotInvocable,
    InterruptTagMismatch,
)
from monet.client._events import RunComplete, RunFailed, RunStarted
from monet.client._run import RunClient
from monet.client._run_state import _RunStore


def _make_core(**overrides: Any) -> _ClientCore:
    defaults: dict[str, Any] = {
        "url": "http://localhost:2026",
        "api_key": None,
        "data_url": "http://localhost:2026",
        "client": MagicMock(),
        "store": _RunStore(),
        "entrypoints": {"default": {"graph": "default"}},
        "graph_roles": {},
    }
    defaults.update(overrides)
    return _ClientCore(**defaults)


@pytest.mark.asyncio
async def test_run_raises_graph_not_invocable() -> None:
    core = _make_core(entrypoints={"x": {"graph": "other"}})
    client = RunClient(core)
    with pytest.raises(GraphNotInvocable):
        async for _ in client.run("unknown"):
            pass


@pytest.mark.asyncio
async def test_run_yields_started_then_complete() -> None:
    core = _make_core()
    client = RunClient(core)

    async def fake_stream(*a: Any, **kw: Any) -> Any:
        return
        yield

    with (
        patch("monet.client._run.create_thread", return_value="tid-1"),
        patch("monet.client._run.stream_run", new=fake_stream),
        patch("monet.client._run.get_state_values", return_value=({}, [])),
    ):
        events = [e async for e in client.run("default", run_id="r1")]

    assert isinstance(events[0], RunStarted)
    assert events[0].run_id == "r1"
    assert isinstance(events[-1], RunComplete)


@pytest.mark.asyncio
async def test_run_yields_failed_on_error_mode() -> None:
    core = _make_core()
    client = RunClient(core)

    async def fake_stream_error(*a: Any, **kw: Any) -> Any:
        yield "error", "boom"

    with (
        patch("monet.client._run.create_thread", return_value="tid-1"),
        patch("monet.client._run.stream_run", new=fake_stream_error),
    ):
        events = [e async for e in client.run("default", run_id="r2")]

    assert isinstance(events[-1], RunFailed)
    assert "boom" in events[-1].error


@pytest.mark.asyncio
async def test_resume_raises_already_resolved_when_no_next() -> None:
    core = _make_core()
    client = RunClient(core)
    client._find_interrupted_thread = AsyncMock(return_value=("tid", "default"))  # type: ignore[method-assign]

    with (
        patch("monet.client._run.get_state_values", return_value=({}, [])),
        pytest.raises(AlreadyResolved),
    ):
        await client.resume("run-1", "tag", {})


@pytest.mark.asyncio
async def test_resume_raises_tag_mismatch() -> None:
    core = _make_core()
    client = RunClient(core)
    client._find_interrupted_thread = AsyncMock(return_value=("tid", "default"))  # type: ignore[method-assign]
    client._await_interrupted_status = AsyncMock()  # type: ignore[method-assign]

    with (
        patch("monet.client._run.get_state_values", return_value=({}, ["actual_tag"])),
        pytest.raises(InterruptTagMismatch),
    ):
        await client.resume("run-1", "wrong_tag", {})


@pytest.mark.asyncio
async def test_abort_calls_stream_run_with_abort_payload() -> None:
    core = _make_core()
    client = RunClient(core)
    client._find_interrupted_thread = AsyncMock(return_value=("tid", "default"))  # type: ignore[method-assign]

    called_with: list[Any] = []

    async def fake_stream(*a: Any, **kw: Any) -> Any:
        called_with.append(kw.get("command"))
        return
        yield

    with patch("monet.client._run.stream_run", new=fake_stream):
        await client.abort("run-1")

    assert called_with == [{"resume": {"action": "abort"}}]
