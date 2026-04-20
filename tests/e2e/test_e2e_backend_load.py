"""E2E-05 — queue backend load test (in-memory + redis streams).

Drives ``N_TASKS`` tasks directly through the ``TaskQueue`` protocol and
asserts every task reaches a terminal state within the deadline. Covers
both shipped backends: ``InMemoryTaskQueue`` (reference) and
``RedisStreamsTaskQueue`` (production). Redis leg runs against a
testcontainer.

Gated behind ``MONET_E2E=1``. This test does not require ``monet dev``
— it exercises the queue contract directly.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from monet.queue import InMemoryTaskQueue, TaskRecord, TaskStatus
from monet.types import AgentResult, AgentRunContext

N_TASKS = 25
P99_LATENCY_SECONDS = 10.0


def _make_task(i: int) -> TaskRecord:
    ctx = AgentRunContext(
        task=f"work-{i}",
        context=[],
        command="default",
        trace_id="load-trace",
        run_id="load-run",
        agent_id="noop",
        skills=[],
    )
    return {
        "schema_version": 1,
        "task_id": f"load-{i}-{uuid.uuid4().hex[:8]}",
        "agent_id": "noop",
        "command": "default",
        "pool": "local",
        "context": ctx,
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": datetime.now(UTC).isoformat(),
        "claimed_at": None,
        "completed_at": None,
    }


async def _drain(queue: Any, n_tasks: int, deadline_s: float) -> list[float]:
    """Enqueue + consume n_tasks; return per-task latencies (monotonic)."""
    latencies: list[float] = []
    enqueue_times: dict[str, float] = {}

    async def producer() -> None:
        for i in range(n_tasks):
            task = _make_task(i)
            enqueue_times[task["task_id"]] = time.monotonic()
            await queue.enqueue(task)

    async def consumer() -> None:
        handled = 0
        deadline = time.monotonic() + deadline_s
        while handled < n_tasks and time.monotonic() < deadline:
            rec = await queue.claim("local", consumer_id="load-w1", block_ms=100)
            if rec is None:
                continue
            result = AgentResult(
                success=True,
                output="ok",
                trace_id=rec["context"]["trace_id"],
                run_id=rec["context"]["run_id"],
            )
            await queue.complete(rec["task_id"], result)
            tid = rec["task_id"]
            if tid in enqueue_times:
                latencies.append(time.monotonic() - enqueue_times[tid])
            handled += 1

    await asyncio.gather(producer(), consumer())
    return latencies


@pytest.mark.e2e
async def test_in_memory_backend_load() -> None:
    queue = InMemoryTaskQueue()
    latencies = await _drain(queue, N_TASKS, deadline_s=60.0)
    assert len(latencies) == N_TASKS, f"only {len(latencies)} of {N_TASKS} completed"
    p99 = sorted(latencies)[max(0, int(len(latencies) * 0.99) - 1)]
    assert p99 < P99_LATENCY_SECONDS, f"p99 latency {p99:.2f}s too high"


@pytest.mark.e2e
async def test_redis_streams_backend_load(redis_container: Any) -> None:
    from monet.queue.backends.redis_streams import RedisStreamsTaskQueue

    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    redis_url = f"redis://{host}:{port}/0"

    queue = RedisStreamsTaskQueue(redis_uri=redis_url)
    try:
        latencies = await _drain(queue, N_TASKS, deadline_s=60.0)
    finally:
        await queue.close()
    assert len(latencies) == N_TASKS, f"only {len(latencies)} of {N_TASKS} completed"
    p99 = sorted(latencies)[max(0, int(len(latencies) * 0.99) - 1)]
    assert p99 < P99_LATENCY_SECONDS, f"p99 latency {p99:.2f}s too high"
