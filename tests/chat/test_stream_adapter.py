"""Tests for _stream_adapter: TUIEvent mapping and subgraph dedup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from monet.cli.chat._stream_adapter import (
    AssistantChunk,
    StreamAdapter,
    TUIEvent,
    _to_tui_event,
)
from monet.client._events import (
    AgentProgress,
    Interrupt,
    NodeUpdate,
    RunComplete,
    RunFailed,
    RunStarted,
    SignalEmitted,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ── helpers ────────────────────────────────────────────────────────────────


async def _collect(events: list[Any]) -> list[TUIEvent]:
    async def _iter() -> AsyncIterator[Any]:
        for e in events:
            yield e

    adapter = StreamAdapter()
    return [ev async for ev in adapter.adapt(_iter())]


def _progress(
    run_id: str = "r1", agent_id: str = "agent", status: str = "running"
) -> AgentProgress:
    return AgentProgress(run_id=run_id, agent_id=agent_id, status=status)


# ── import smoke ───────────────────────────────────────────────────────────


def test_import_smoke() -> None:
    """All public names importable from monet.client._events (not a stray path)."""
    from monet.cli.chat._stream_adapter import (  # noqa: F401
        AssistantChunk,
        StreamAdapter,
        TUIEvent,
    )


# ── _to_tui_event unit ─────────────────────────────────────────────────────


def test_to_tui_event_agent_progress() -> None:
    ev = _progress()
    assert _to_tui_event(ev) is ev


def test_to_tui_event_interrupt() -> None:
    ev = Interrupt(run_id="r", tag="review")
    assert _to_tui_event(ev) is ev


def test_to_tui_event_run_complete() -> None:
    ev = RunComplete(run_id="r")
    assert _to_tui_event(ev) is ev


def test_to_tui_event_run_failed() -> None:
    ev = RunFailed(run_id="r", error="boom")
    assert _to_tui_event(ev) is ev


def test_to_tui_event_string_becomes_assistant_chunk() -> None:
    result = _to_tui_event("hello")
    assert result == AssistantChunk(text="hello")


def test_to_tui_event_node_update_with_dict_message() -> None:
    ev = NodeUpdate(run_id="r", node="chat", update={"messages": [{"content": "hi"}]})
    assert _to_tui_event(ev) == AssistantChunk(text="hi")


def test_to_tui_event_node_update_with_object_message() -> None:
    class _Msg:
        content = "from object"

    ev = NodeUpdate(run_id="r", node="chat", update={"messages": [_Msg()]})
    assert _to_tui_event(ev) == AssistantChunk(text="from object")


def test_to_tui_event_node_update_no_messages_returns_none() -> None:
    ev = NodeUpdate(run_id="r", node="router", update={"next": "agent"})
    assert _to_tui_event(ev) is None


def test_to_tui_event_node_update_empty_content_returns_none() -> None:
    ev = NodeUpdate(run_id="r", node="chat", update={"messages": [{"content": ""}]})
    assert _to_tui_event(ev) is None


def test_to_tui_event_unknown_type_returns_none() -> None:
    assert _to_tui_event(42) is None
    assert _to_tui_event({"role": "assistant"}) is None


def test_to_tui_event_run_started_discarded() -> None:
    ev = RunStarted(run_id="r", graph_id="chat", thread_id="t")
    assert _to_tui_event(ev) is None


def test_to_tui_event_signal_emitted_discarded() -> None:
    ev = SignalEmitted(run_id="r", agent_id="a", signal_type="done")
    assert _to_tui_event(ev) is None


# ── StreamAdapter dedup ────────────────────────────────────────────────────


async def test_single_event_passes_through() -> None:
    ev = _progress()
    result = await _collect([ev])
    assert result == [ev]


async def test_duplicate_subgraph_event_deduped_to_one() -> None:
    """Same AgentProgress emitted twice (subgraph re-emit) → yields once."""
    ev = _progress(status="running")
    result = await _collect([ev, ev])
    assert result == [ev]


async def test_two_different_subgraph_events_both_pass() -> None:
    """Two distinct AgentProgress events → both yielded."""
    ev1 = _progress(status="running")
    ev2 = _progress(status="done")
    result = await _collect([ev1, ev2])
    assert result == [ev1, ev2]


async def test_dedup_only_within_same_adapter_instance() -> None:
    """New StreamAdapter instance → fresh dedup state."""
    ev = _progress()
    r1 = await _collect([ev])
    r2 = await _collect([ev])
    assert r1 == [ev]
    assert r2 == [ev]


async def test_identical_text_chunks_not_deduped() -> None:
    """AssistantChunk events are never deduped — text has sequence semantics."""
    result = await _collect(["hello", "hello"])
    assert result == [AssistantChunk("hello"), AssistantChunk("hello")]


async def test_mixed_stream_dedup_only_non_chunk() -> None:
    """Interleaved text chunks and duplicate progress events."""
    ev = _progress()
    result = await _collect(["chunk1", ev, "chunk2", ev, "chunk3"])
    assert result == [
        AssistantChunk("chunk1"),
        ev,
        AssistantChunk("chunk2"),
        AssistantChunk("chunk3"),
    ]


async def test_unknown_events_filtered() -> None:
    ev = _progress()
    result = await _collect([RunStarted(run_id="r", graph_id="g", thread_id="t"), ev])
    assert result == [ev]


async def test_three_distinct_agent_progress_all_pass() -> None:
    ev1 = _progress(agent_id="a", status="running")
    ev2 = _progress(agent_id="b", status="running")
    ev3 = _progress(agent_id="a", status="done")
    result = await _collect([ev1, ev2, ev3])
    assert result == [ev1, ev2, ev3]


async def test_dedup_run_complete() -> None:
    ev = RunComplete(run_id="r", final_values={"x": 1})
    result = await _collect([ev, ev])
    assert result == [ev]


async def test_dedup_run_failed() -> None:
    ev = RunFailed(run_id="r", error="timeout")
    result = await _collect([ev, ev])
    assert result == [ev]
