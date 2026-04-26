"""Tests for core.engine.execute_task."""

from __future__ import annotations

import asyncio
from typing import Any

from monet.core.engine import execute_task
from monet.core.registry import LocalRegistry
from monet.core.stubs import _current_task_id, _progress_publisher, _progress_writer_cv
from monet.events import TASK_RECORD_SCHEMA_VERSION, TaskRecord, TaskStatus
from monet.types import AgentResult, AgentRunContext

# --- Helpers ---


def _task(
    task_id: str = "task-1",
    agent_id: str = "test-agent",
    command: str = "fast",
    run_id: str = "run-1",
) -> TaskRecord:
    ctx: AgentRunContext = {
        "task": "do it",
        "context": [],
        "command": command,
        "trace_id": "",
        "run_id": run_id,
        "agent_id": agent_id,
        "skills": [],
    }
    return TaskRecord(
        schema_version=TASK_RECORD_SCHEMA_VERSION,
        task_id=task_id,
        agent_id=agent_id,
        command=command,
        pool="local",
        context=ctx,
        status=TaskStatus.CLAIMED,
        result=None,
        created_at="2026-01-01T00:00:00Z",
        claimed_at="2026-01-01T00:00:00Z",
        completed_at=None,
    )


def _result(success: bool = True) -> AgentResult:
    return AgentResult(
        success=success,
        output="ok",
        artifacts=(),
        signals=(),
        trace_id="",
        run_id="run-1",
    )


class RecordingQueue:
    """Minimal queue spy for execute_task tests."""

    def __init__(self) -> None:
        self.completed: list[tuple[str, Any]] = []
        self.failed: list[tuple[str, str]] = []

    async def complete(self, task_id: str, result: Any) -> None:
        self.completed.append((task_id, result))

    async def fail(self, task_id: str, error: str) -> None:
        self.failed.append((task_id, error))

    async def publish_progress(self, task_id: str, data: dict[str, Any]) -> None:
        pass


# --- Tests ---


async def test_successful_handler_calls_complete() -> None:
    registry = LocalRegistry()
    queue = RecordingQueue()
    expected = _result()

    async def handler(ctx: AgentRunContext) -> AgentResult:
        return expected

    registry.register("test-agent", "fast", handler)
    await execute_task(_task(), registry, queue)  # type: ignore[arg-type]

    assert queue.completed == [("task-1", expected)]
    assert queue.failed == []


async def test_no_handler_calls_fail() -> None:
    registry = LocalRegistry()
    queue = RecordingQueue()

    await execute_task(_task(), registry, queue)  # type: ignore[arg-type]

    assert queue.completed == []
    assert len(queue.failed) == 1
    task_id, msg = queue.failed[0]
    assert task_id == "task-1"
    assert "No handler" in msg
    assert "test-agent" in msg


async def test_handler_timeout_calls_fail() -> None:
    registry = LocalRegistry()
    queue = RecordingQueue()

    async def slow_handler(ctx: AgentRunContext) -> AgentResult:
        await asyncio.sleep(10)
        return _result()

    registry.register("test-agent", "fast", slow_handler)
    await execute_task(_task(), registry, queue, task_timeout=0.01)  # type: ignore[arg-type]

    assert queue.completed == []
    assert len(queue.failed) == 1
    _, msg = queue.failed[0]
    assert "timed out" in msg


async def test_handler_raises_calls_fail() -> None:
    registry = LocalRegistry()
    queue = RecordingQueue()

    async def bad_handler(ctx: AgentRunContext) -> AgentResult:
        raise ValueError("something broke")

    registry.register("test-agent", "fast", bad_handler)
    await execute_task(_task(), registry, queue)  # type: ignore[arg-type]

    assert queue.completed == []
    assert len(queue.failed) == 1
    _, msg = queue.failed[0]
    assert "ValueError" in msg
    assert "something broke" in msg


async def test_contextvars_set_during_execution_reset_after() -> None:
    registry = LocalRegistry()
    queue = RecordingQueue()
    captured: dict[str, Any] = {}

    sentinel_publisher = lambda data: None  # noqa: E731

    async def handler(ctx: AgentRunContext) -> AgentResult:
        captured["task_id"] = _current_task_id.get()
        captured["publisher"] = _progress_publisher.get()
        captured["writer"] = _progress_writer_cv.get()
        return _result()

    registry.register("test-agent", "fast", handler)

    # Ensure ContextVars start at their defaults
    assert _current_task_id.get() == ""
    assert _progress_publisher.get() is None

    await execute_task(  # type: ignore[arg-type]
        _task(task_id="task-42"),
        registry,
        queue,
        publisher=sentinel_publisher,
        writer=None,
    )

    # ContextVars were set correctly during handler execution
    assert captured["task_id"] == "task-42"
    assert captured["publisher"] is sentinel_publisher
    assert captured["writer"] is None

    # ContextVars reset to defaults after execute_task returns
    assert _current_task_id.get() == ""
    assert _progress_publisher.get() is None


async def test_contextvars_reset_after_failure() -> None:
    registry = LocalRegistry()
    queue = RecordingQueue()

    async def bad_handler(ctx: AgentRunContext) -> AgentResult:
        raise RuntimeError("boom")

    registry.register("test-agent", "fast", bad_handler)
    await execute_task(_task(), registry, queue)  # type: ignore[arg-type]

    assert _current_task_id.get() == ""
    assert _progress_publisher.get() is None


async def test_on_before_complete_called_before_complete() -> None:
    registry = LocalRegistry()
    queue = RecordingQueue()
    call_order: list[str] = []

    original_complete = queue.complete

    async def tracking_complete(task_id: str, result: Any) -> None:
        call_order.append("complete")
        await original_complete(task_id, result)

    queue.complete = tracking_complete  # type: ignore[method-assign]

    async def before() -> None:
        call_order.append("before")

    async def handler(ctx: AgentRunContext) -> AgentResult:
        return _result()

    registry.register("test-agent", "fast", handler)
    await execute_task(_task(), registry, queue, on_before_complete=before)  # type: ignore[arg-type]

    assert call_order == ["before", "complete"]


async def test_on_before_complete_called_before_fail_on_timeout() -> None:
    registry = LocalRegistry()
    queue = RecordingQueue()
    call_order: list[str] = []

    original_fail = queue.fail

    async def tracking_fail(task_id: str, error: str) -> None:
        call_order.append("fail")
        await original_fail(task_id, error)

    queue.fail = tracking_fail  # type: ignore[method-assign]

    async def before() -> None:
        call_order.append("before")

    async def slow_handler(ctx: AgentRunContext) -> AgentResult:
        await asyncio.sleep(10)
        return _result()

    registry.register("test-agent", "fast", slow_handler)
    await execute_task(  # type: ignore[arg-type]
        _task(), registry, queue, task_timeout=0.01, on_before_complete=before
    )

    assert call_order == ["before", "fail"]


async def test_on_before_complete_called_before_fail_on_exception() -> None:
    registry = LocalRegistry()
    queue = RecordingQueue()
    call_order: list[str] = []

    original_fail = queue.fail

    async def tracking_fail(task_id: str, error: str) -> None:
        call_order.append("fail")
        await original_fail(task_id, error)

    queue.fail = tracking_fail  # type: ignore[method-assign]

    async def before() -> None:
        call_order.append("before")

    async def bad_handler(ctx: AgentRunContext) -> AgentResult:
        raise RuntimeError("boom")

    registry.register("test-agent", "fast", bad_handler)
    await execute_task(_task(), registry, queue, on_before_complete=before)  # type: ignore[arg-type]

    assert call_order == ["before", "fail"]


async def test_otel_span_created() -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider.add_span_processor(SimpleSpanProcessor(exporter))

    import monet.core.engine as engine_mod

    original_tracer = engine_mod._tracer
    engine_mod._tracer = provider.get_tracer("test")
    try:
        registry = LocalRegistry()
        queue = RecordingQueue()

        async def handler(ctx: AgentRunContext) -> AgentResult:
            return _result()

        registry.register("test-agent", "fast", handler)
        await execute_task(_task(), registry, queue)  # type: ignore[arg-type]
    finally:
        engine_mod._tracer = original_tracer

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "worker.execute.test-agent.fast"
    assert span.attributes is not None
    assert span.attributes.get("agent.id") == "test-agent"
    assert span.attributes.get("agent.command") == "fast"
    assert span.attributes.get("task.id") == "task-1"
