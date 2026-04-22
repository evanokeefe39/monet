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
    """_forward_progress delivers pre-enriched events (with run_id) to the writer."""
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
    # Start _forward_progress BEFORE publishing so it subscribes first.
    fwd = asyncio.create_task(_forward_progress(queue, task_id, writer=captured.append))
    await asyncio.sleep(0)  # yield so fwd starts

    # Worker enriches events before publish; simulate that here.
    await queue.publish_progress(
        task_id, {"status": "running", "agent": "test-agent", "run_id": run_id}
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
    """_forward_progress passes all event fields through to the writer unchanged."""
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

    fwd = asyncio.create_task(_forward_progress(queue, task_id, writer=captured.append))
    await asyncio.sleep(0)

    # Worker enriches events before publish; simulate that here.
    await queue.publish_progress(
        task_id,
        {"status": "writing", "agent": "writer", "extra": 42, "run_id": run_id},
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


async def test_forward_progress_noop_without_writer(
    queue: InMemoryTaskQueue,
) -> None:
    """_forward_progress with writer=None exits immediately without touching the queue."""
    task_id = "t-3"
    # No enqueue — if it tried to subscribe it would block or error.
    fwd = asyncio.create_task(_forward_progress(queue, task_id, writer=None))
    await asyncio.wait_for(fwd, timeout=1.0)
    # Verify nothing was subscribed.
    assert task_id not in queue._progress_subscribers


async def test_invoke_agent_emits_lifecycle_events() -> None:
    """invoke_agent must emit agent:started and agent:completed/failed.

    Uses the autouse queue+worker from conftest.  A test agent registered
    via ``@agent`` returns a failing result so we can assert both lifecycle
    bookends.
    """
    from monet.orchestration._invoke import (
        AGENT_FAILED_STATUS,
        AGENT_STARTED_STATUS,
        invoke_agent,
    )

    captured: list[dict[str, Any]] = []

    with patch(
        "monet.orchestration._invoke._emit_lifecycle",
        side_effect=captured.append,
    ):
        result = await invoke_agent(
            "test-lifecycle", command="fast", task="lifecycle test"
        )

    # Agent doesn't exist → dispatch_failed signal → lifecycle: started + failed
    assert not result.success
    assert len(captured) == 2
    assert captured[0]["status"] == AGENT_STARTED_STATUS
    assert captured[0]["agent"] == "test-lifecycle"
    assert captured[0]["command"] == "fast"
    assert captured[1]["status"] == AGENT_FAILED_STATUS
    assert captured[1]["agent"] == "test-lifecycle"


async def test_progress_history_retrievable_by_langgraph_run_id(
    queue: InMemoryTaskQueue,
) -> None:
    """Progress events published with a LangGraph run_id are retrievable by that id.

    This is the core regression test: get_thread_progress queries by
    LangGraph run_id (from list_thread_runs). Events must be stored under
    that same id via publish_progress dual-index write.
    """
    task_id = "task-lg-rt"
    lg_run_id = "lg-run-uuid4-style"

    from monet.types import AgentResult

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
                "run_id": lg_run_id,
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

    await queue.publish_progress(
        task_id, {"status": "running", "agent": "test-agent", "run_id": lg_run_id}
    )
    await queue.complete(
        task_id,
        AgentResult(success=True, output="", signals=(), trace_id="", run_id=lg_run_id),
    )

    history = await queue.get_progress_history(lg_run_id)
    assert len(history) >= 1
    assert all(ev.get("run_id") == lg_run_id for ev in history)


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
