"""Tests for the SQLite-backed persistent task queue."""

from __future__ import annotations

import asyncio

import pytest

from monet.queue import SQLiteTaskQueue, TaskStatus
from monet.types import (
    AgentResult,
    AgentRunContext,
    ArtifactPointer,
    Signal,
    SignalType,
)


def _make_ctx(agent_id: str = "test-agent", command: str = "fast") -> AgentRunContext:
    return AgentRunContext(
        task="do something",
        context=[],
        command=command,
        trace_id="t-1",
        run_id="r-1",
        agent_id=agent_id,
        skills=[],
    )


@pytest.fixture
async def queue(tmp_path: object) -> SQLiteTaskQueue:
    """Provide a file-backed SQLite queue for each test."""
    # tmp_path is a pathlib.Path provided by pytest
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "test_queue.db"
    q = SQLiteTaskQueue(db_path=db_path, lease_ttl=300)
    await q.initialize()
    yield q  # type: ignore[misc]
    await q.close()


@pytest.fixture
async def mem_queue() -> SQLiteTaskQueue:
    """In-memory SQLite queue for faster tests."""
    q = SQLiteTaskQueue(db_path=":memory:", lease_ttl=300)
    await q.initialize()
    yield q  # type: ignore[misc]
    await q.close()


async def test_enqueue_claim_complete_cycle(mem_queue: SQLiteTaskQueue) -> None:
    q = mem_queue
    ctx = _make_ctx()
    task_id = await q.enqueue("test-agent", "fast", ctx)
    assert task_id

    record = await q.claim("local")
    assert record is not None
    assert record["task_id"] == task_id
    assert record["status"] == TaskStatus.CLAIMED
    assert record["pool"] == "local"

    result = AgentResult(success=True, output="done", trace_id="t-1", run_id="r-1")
    await q.complete(task_id, result)

    polled = await q.poll_result(task_id, timeout=1.0)
    assert polled.success is True
    assert polled.output == "done"


async def test_poll_result_timeout(mem_queue: SQLiteTaskQueue) -> None:
    q = mem_queue
    ctx = _make_ctx()
    task_id = await q.enqueue("test-agent", "fast", ctx)

    with pytest.raises(TimeoutError):
        await q.poll_result(task_id, timeout=0.05)


async def test_poll_result_unknown_task_id(mem_queue: SQLiteTaskQueue) -> None:
    q = mem_queue
    with pytest.raises(KeyError, match="Unknown task_id"):
        await q.poll_result("nonexistent", timeout=0.1)


async def test_claim_returns_none_when_empty(mem_queue: SQLiteTaskQueue) -> None:
    q = mem_queue
    record = await q.claim("local")
    assert record is None


async def test_pool_isolation(mem_queue: SQLiteTaskQueue) -> None:
    """Tasks in pool A are not visible to workers polling pool B."""
    q = mem_queue
    await q.enqueue("agent-a", "fast", _make_ctx("agent-a"), pool="cloud")

    # Claim for local pool -- should return None.
    record = await q.claim("local")
    assert record is None

    # Claim for cloud pool -- should get it.
    record = await q.claim("cloud")
    assert record is not None
    assert record["agent_id"] == "agent-a"
    assert record["pool"] == "cloud"


async def test_cancel_pending_task(mem_queue: SQLiteTaskQueue) -> None:
    q = mem_queue
    task_id = await q.enqueue("agent", "fast", _make_ctx())
    await q.cancel(task_id)

    result = await q.poll_result(task_id, timeout=1.0)
    assert result.success is False


async def test_cancel_completed_task_is_noop(mem_queue: SQLiteTaskQueue) -> None:
    q = mem_queue
    task_id = await q.enqueue("agent", "fast", _make_ctx())
    record = await q.claim("local")
    assert record is not None
    await q.complete(task_id, AgentResult(success=True, trace_id="t", run_id="r"))
    await q.cancel(task_id)
    result = await q.poll_result(task_id, timeout=1.0)
    assert result.success is True


async def test_fail_sets_error_and_status(mem_queue: SQLiteTaskQueue) -> None:
    q = mem_queue
    ctx = _make_ctx()
    task_id = await q.enqueue("test-agent", "fast", ctx)
    record = await q.claim("local")
    assert record is not None

    await q.fail(task_id, "something went wrong")
    result = await q.poll_result(task_id, timeout=1.0)
    assert result.success is False
    assert result.has_signal(SignalType.SEMANTIC_ERROR)


async def test_result_preserves_artifacts_and_signals(
    mem_queue: SQLiteTaskQueue,
) -> None:
    """Round-trip serialization preserves artifacts and signals."""
    q = mem_queue
    task_id = await q.enqueue("agent", "fast", _make_ctx())
    await q.claim("local")

    original = AgentResult(
        success=True,
        output={"key": "value"},
        artifacts=(ArtifactPointer(artifact_id="a1", url="s3://bucket/a1"),),
        signals=(
            Signal(
                type=SignalType.LOW_CONFIDENCE,
                reason="not sure",
                metadata={"score": 0.3},
            ),
        ),
        trace_id="t-1",
        run_id="r-1",
    )
    await q.complete(task_id, original)

    result = await q.poll_result(task_id, timeout=1.0)
    assert result.success is True
    assert result.output == {"key": "value"}
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["artifact_id"] == "a1"
    assert len(result.signals) == 1
    assert result.signals[0]["type"] == SignalType.LOW_CONFIDENCE


async def test_lease_expiry_sweeper_requeues(
    mem_queue: SQLiteTaskQueue,
) -> None:
    """Claimed tasks with expired leases are requeued by the sweeper."""
    q = mem_queue
    # Use a very short lease TTL so we can test expiry.
    q._lease_ttl = 0  # Lease expires immediately.

    task_id = await q.enqueue("agent", "fast", _make_ctx())
    record = await q.claim("local")
    assert record is not None
    assert record["task_id"] == task_id

    # The task is now claimed with an already-expired lease (ttl=0).
    # Run a single sweep cycle.
    await q._sweep_expired_leases()

    # Task should be claimable again.
    record2 = await q.claim("local")
    assert record2 is not None
    assert record2["task_id"] == task_id


async def test_concurrent_enqueue_and_claim(
    mem_queue: SQLiteTaskQueue,
) -> None:
    """Multiple concurrent enqueue + claim operations do not lose tasks."""
    q = mem_queue
    n_tasks = 20

    # Enqueue all tasks.
    task_ids = []
    for _i in range(n_tasks):
        tid = await q.enqueue("agent", "fast", _make_ctx("agent"))
        task_ids.append(tid)

    # Claim all concurrently.
    claimed: list[str] = []

    async def claimer() -> None:
        while True:
            record = await q.claim("local")
            if record is None:
                break
            claimed.append(record["task_id"])

    # Run multiple claimers concurrently.
    await asyncio.gather(claimer(), claimer(), claimer())

    # Every task should be claimed exactly once.
    assert sorted(claimed) == sorted(task_ids)


async def test_file_backed_persistence(tmp_path: object) -> None:
    """Data survives close + reopen of the database."""
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "persist.db"

    # First connection: enqueue a task.
    q1 = SQLiteTaskQueue(db_path=db_path)
    await q1.initialize()
    ctx = _make_ctx()
    task_id = await q1.enqueue("agent", "fast", ctx)
    await q1.close()

    # Second connection: task should be claimable.
    q2 = SQLiteTaskQueue(db_path=db_path)
    await q2.initialize()
    record = await q2.claim("local")
    assert record is not None
    assert record["task_id"] == task_id
    await q2.close()
