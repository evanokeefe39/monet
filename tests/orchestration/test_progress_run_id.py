"""Regression tests for progress event run_id attribution.

Ensures that run_id is correctly injected at every emission hop so
clients can attribute [progress] lines to the correct run.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from monet.orchestration._invoke import _forward_progress
from monet.queue.backends.memory import InMemoryTaskQueue


@pytest.fixture
def queue() -> InMemoryTaskQueue:
    return InMemoryTaskQueue()


async def test_forward_progress_injects_run_id(queue: InMemoryTaskQueue) -> None:
    """_forward_progress must stamp run_id onto every emitted event."""
    task_id = "t-1"
    run_id = "run-abc"

    from monet.types import AgentResult

    # Enqueue, publish a progress event, then complete — _forward_progress drains.
    await queue.enqueue(
        {
            "schema_version": 1,
            "task_id": task_id,
            "agent_id": "test-agent",
            "command": "fast",
            "pool": "local",
            "context": {
                "task": "",
                "context": [],
                "command": "fast",
                "trace_id": "",
                "run_id": run_id,
                "agent_id": "test-agent",
                "skills": [],
            },
            "status": "pending",
            "result": None,
            "created_at": "2024-01-01T00:00:00Z",
            "claimed_at": None,
            "completed_at": None,
        }
    )

    captured: list[dict[str, Any]] = []
    with patch("monet.core.stubs.emit_progress", side_effect=captured.append):
        # Start _forward_progress BEFORE publishing so it subscribes first.
        fwd = asyncio.create_task(_forward_progress(queue, task_id, run_id))
        await asyncio.sleep(0)  # yield so fwd starts

        await queue.publish_progress(
            task_id, {"status": "running", "agent": "test-agent"}
        )
        await queue.complete(
            task_id,
            AgentResult(
                success=True, output="", signals=(), trace_id="", run_id=run_id
            ),
        )
        # _forward_progress should exit naturally now that task is terminal.
        await asyncio.wait_for(fwd, timeout=2.0)

    assert len(captured) >= 1
    for ev in captured:
        assert ev.get("run_id") == run_id, f"run_id missing in {ev}"


async def test_forward_progress_preserves_existing_fields(
    queue: InMemoryTaskQueue,
) -> None:
    """_forward_progress preserves all original event fields, not just run_id."""
    task_id = "t-2"
    run_id = "run-xyz"

    captured: list[dict[str, Any]] = []

    from monet.types import AgentResult

    await queue.enqueue(
        {
            "schema_version": 1,
            "task_id": task_id,
            "agent_id": "a",
            "command": "fast",
            "pool": "local",
            "context": {
                "task": "",
                "context": [],
                "command": "fast",
                "trace_id": "",
                "run_id": run_id,
                "agent_id": "a",
                "skills": [],
            },
            "status": "pending",
            "result": None,
            "created_at": "2024-01-01T00:00:00Z",
            "claimed_at": None,
            "completed_at": None,
        }
    )

    with patch("monet.core.stubs.emit_progress", side_effect=captured.append):
        fwd = asyncio.create_task(_forward_progress(queue, task_id, run_id))
        await asyncio.sleep(0)

        await queue.publish_progress(
            task_id,
            {"status": "writing", "agent": "writer", "extra": 42},
        )
        await queue.complete(
            task_id,
            AgentResult(
                success=True, output="", signals=(), trace_id="", run_id=run_id
            ),
        )
        await asyncio.wait_for(fwd, timeout=2.0)

    assert len(captured) >= 1
    ev = captured[0]
    assert ev["status"] == "writing"
    assert ev["agent"] == "writer"
    assert ev["extra"] == 42
    assert ev["run_id"] == run_id


async def test_agent_node_includes_run_id_in_emit_progress() -> None:
    """agent_node must include run_id in failure emit_progress calls."""
    from monet.orchestration.execution_graph import (
        AGENT_FAILED_EVENT_STATUS,
        agent_node,
    )

    captured: list[dict[str, Any]] = []

    with (
        patch(
            "monet.orchestration.execution_graph.emit_progress",
            side_effect=captured.append,
        ),
        patch("monet.orchestration.execution_graph.invoke_agent") as mock_invoke,
    ):
        from monet.signals import SignalType
        from monet.types import AgentResult, Signal

        mock_invoke.return_value = AgentResult(
            success=False,
            output="",
            signals=(
                Signal(
                    type=SignalType.SEMANTIC_ERROR,
                    reason="test fail",
                    metadata=None,
                ),
            ),
            trace_id="",
            run_id="run-node",
        )

        from monet.orchestration.execution_graph import NodeItem

        item = NodeItem(
            node_id="n1",
            agent_id="writer",
            command="fast",
            work_brief_pointer={"artifact_id": "art-1", "url": ""},
            upstream_results=[],
            trace_id="",
            run_id="run-node",
            trace_carrier={},
            thread_id="",
        )
        await agent_node(item)

    assert len(captured) == 1
    ev = captured[0]
    assert ev["status"] == AGENT_FAILED_EVENT_STATUS
    assert ev["run_id"] == "run-node"


async def test_stream_chat_uses_run_id_from_event() -> None:
    """_stream_chat_with_input must use run_id from the custom event."""
    from monet.client.chat import ChatClient

    progress_events = [
        {"status": "running", "agent": "writer", "run_id": "run-chat"},
    ]

    async def _fake_stream(*args: Any, **kwargs: Any):  # type: ignore[return]
        yield ("custom", progress_events[0])

    client_mock = MagicMock()
    chat = ChatClient(client_mock, chat_graph_id="chat")

    results = []
    with (
        patch.object(
            chat, "_stream_chat_with_input", wraps=chat._stream_chat_with_input
        ),
        patch(
            "monet.client.chat.stream_run",
            side_effect=lambda *a, **kw: _fake_stream(),
        ),
    ):
        async for item in chat._stream_chat_with_input(
            "thread-1", input={"messages": []}
        ):
            results.append(item)

    assert len(results) == 1
    from monet.client._events import AgentProgress

    assert isinstance(results[0], AgentProgress)
    assert results[0].run_id == "run-chat"
