"""Tests for progress streaming through the task queue."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from monet.queue import InMemoryTaskQueue, TaskRecord, TaskStatus
from monet.types import AgentResult, AgentRunContext


def _ctx(agent_id: str = "a", command: str = "fast") -> AgentRunContext:
    return AgentRunContext(
        task="",
        context=[],
        command=command,
        trace_id="t",
        run_id="r",
        agent_id=agent_id,
        skills=[],
    )


def _make_task(
    agent_id: str = "a", command: str = "fast", pool: str = "local"
) -> TaskRecord:
    return {
        "schema_version": 1,
        "task_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "command": command,
        "pool": pool,
        "context": _ctx(agent_id=agent_id, command=command),
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": datetime.now(UTC).isoformat(),
        "claimed_at": None,
        "completed_at": None,
    }


# --- InMemoryTaskQueue round-trip ---


async def test_publish_subscribe_round_trip() -> None:
    """Published events reach subscribers."""
    queue = InMemoryTaskQueue()
    task_id = await queue.enqueue(_make_task())

    events: list[dict[str, Any]] = []

    async def consumer() -> None:
        async for ev in queue.subscribe_progress(task_id):
            events.append(ev)

    consumer_task = asyncio.create_task(consumer())
    # Let the subscriber register.
    await asyncio.sleep(0)

    await queue.publish_progress(task_id, {"status": "running"})
    await queue.publish_progress(task_id, {"status": "step_2"})

    # Complete the task to terminate the subscription.
    await queue.complete(task_id, AgentResult(success=True, output="done"))
    await asyncio.wait_for(consumer_task, timeout=2.0)

    assert any({"status": "running"}.items() <= e.items() for e in events)
    assert any({"status": "step_2"}.items() <= e.items() for e in events)


async def test_subscribe_cleanup_after_iteration() -> None:
    """Subscribers removed from internal tracking after terminal state."""
    queue = InMemoryTaskQueue()
    task_id = await queue.enqueue(_make_task())

    async def consumer() -> None:
        async for _ in queue.subscribe_progress(task_id):
            pass

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0)
    await queue.complete(task_id, AgentResult(success=True, output="done"))
    await asyncio.wait_for(consumer_task, timeout=2.0)

    # No subscriber queues retained for this task_id.
    assert task_id not in queue._progress_subscribers


async def test_publish_drops_on_full_subscriber() -> None:
    """Publishing more than the subscriber queue max drops silently."""
    queue = InMemoryTaskQueue()
    task_id = await queue.enqueue(_make_task())

    # Create a stalled subscriber by not reading.
    sub_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2)
    queue._progress_subscribers[task_id].add(sub_q)

    # Publish more than maxsize — should not raise.
    await queue.publish_progress(task_id, {"i": 0})
    await queue.publish_progress(task_id, {"i": 1})
    await queue.publish_progress(task_id, {"i": 2})  # drops
    await queue.publish_progress(task_id, {"i": 3})  # drops

    assert sub_q.qsize() == 2


async def test_subscribe_on_terminated_task_returns_immediately() -> None:
    """Subscribing after task termination returns zero events."""
    queue = InMemoryTaskQueue()
    task_id = await queue.enqueue(_make_task())
    await queue.complete(task_id, AgentResult(success=True, output="done"))

    events: list[dict[str, Any]] = []
    async for ev in queue.subscribe_progress(task_id):
        events.append(ev)
    assert events == []


# --- Sleep(0) race — events published just before terminal state ---


async def test_sleep_zero_flush_before_return() -> None:
    """Events queued after terminal status but before iterator checks
    must still be drained by the sleep(0) dance in subscribe_progress.
    """
    queue = InMemoryTaskQueue()
    task_id = await queue.enqueue(_make_task())

    received: list[dict[str, Any]] = []

    async def consumer() -> None:
        async for ev in queue.subscribe_progress(task_id):
            received.append(ev)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0)  # register subscriber

    # Race: publish then complete immediately. The subscriber may notice
    # completion before consuming the published event. The sleep(0) +
    # drain-nowait pattern ensures it still gets through.
    await queue.publish_progress(task_id, {"final": True})
    await queue.complete(task_id, AgentResult(success=True, output="done"))

    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert any({"final": True}.items() <= e.items() for e in received)


# --- RemoteQueue subscribe raises NotImplementedError ---


async def test_remote_queue_subscribe_raises() -> None:
    """RemoteQueue.subscribe_progress must raise NotImplementedError."""
    from monet.core.worker_client import RemoteQueue, WorkerClient

    client = WorkerClient("http://example.com", "key")
    rq = RemoteQueue(client, pool="p")
    with pytest.raises(NotImplementedError, match="subscribe_progress"):
        await rq.subscribe_progress("t-1")
    await client.close()


# --- _forward_progress suppresses NotImplementedError ---


async def test_forward_progress_suppresses_not_implemented() -> None:
    """_forward_progress must not raise when backend lacks subscription."""
    from monet.orchestration._invoke import _forward_progress

    class NotImplQueue:
        def subscribe_progress(self, task_id: str) -> Any:
            raise NotImplementedError("nope")

    # Should complete without raising.
    await _forward_progress(NotImplQueue(), "t-1")  # type: ignore[arg-type]


# --- Worker drain flushes on cancellation ---


async def test_worker_publishes_emit_progress_events() -> None:
    """Worker wires _progress_publisher so emit_progress() forwards to queue."""
    from monet.core.registry import LocalRegistry
    from monet.core.stubs import emit_progress
    from monet.queue import run_worker

    queue = InMemoryTaskQueue()
    registry = LocalRegistry()

    async def my_agent(ctx: AgentRunContext) -> AgentResult:
        emit_progress({"phase": "work", "run_id": ctx["run_id"]})
        return AgentResult(success=True, output="done")

    registry.register("my-agent", "go", my_agent)

    events: list[dict[str, Any]] = []
    task_id = await queue.enqueue(_make_task(agent_id="my-agent", command="go"))

    async def consumer() -> None:
        async for ev in queue.subscribe_progress(task_id):
            events.append(ev)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    worker_task = asyncio.create_task(run_worker(queue, registry, pool="local"))
    import contextlib

    from monet.orchestration._invoke import wait_completion

    try:
        result = await wait_completion(queue, task_id, timeout=5.0)
        await asyncio.wait_for(consumer_task, timeout=2.0)
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task

    assert result.success
    assert any(e.get("phase") == "work" for e in events)
