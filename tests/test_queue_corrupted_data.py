"""Tests for queue backend resilience to corrupted JSON data.

Covers SQLite (direct SQL injection) and InMemory (dict patching).
Redis and Upstash require external services and are tested separately.
"""

from __future__ import annotations

import logging

import pytest  # noqa: TC002

from monet.queue import InMemoryTaskQueue, SQLiteTaskQueue, TaskStatus
from monet.types import AgentResult, AgentRunContext


def _make_ctx(agent_id: str = "test-agent") -> AgentRunContext:
    return AgentRunContext(
        task="do something",
        context=[],
        command="fast",
        trace_id="t-1",
        run_id="r-1",
        agent_id=agent_id,
        skills=[],
    )


# --- SQLite: corrupted context JSON ---


async def test_sqlite_fail_with_corrupt_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """fail() should not crash when stored context JSON is corrupted."""
    q = SQLiteTaskQueue(":memory:")
    await q.initialize()

    task_id = await q.enqueue("agent-a", "fast", _make_ctx())

    # Corrupt the context via raw SQL.
    assert q._db is not None
    await q._db.execute(
        "UPDATE tasks SET context = ? WHERE task_id = ?",
        ("not-valid-json", task_id),
    )
    await q._db.commit()

    # Claim the task so it's in CLAIMED state for fail().
    record = await q.claim("local")
    assert record is not None

    with caplog.at_level(logging.WARNING):
        await q.fail(task_id, "test error")

    # Should have logged the corruption.
    assert "Corrupt context JSON" in caplog.text

    # Task should still be marked as failed.
    assert q._db is not None
    async with q._db.execute(
        "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == TaskStatus.FAILED

    await q.close()


async def test_sqlite_claim_with_corrupt_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """claim() should return a record even with corrupt context JSON."""
    q = SQLiteTaskQueue(":memory:")
    await q.initialize()

    task_id = await q.enqueue("agent-a", "fast", _make_ctx())

    # Corrupt context before claiming.
    assert q._db is not None
    await q._db.execute(
        "UPDATE tasks SET context = ? WHERE task_id = ?",
        ("{broken", task_id),
    )
    await q._db.commit()

    with caplog.at_level(logging.WARNING):
        record = await q.claim("local")

    assert record is not None
    assert record["task_id"] == task_id
    # Context should be empty dict, not crash.
    assert record["context"] == {}  # type: ignore[comparison-overlap]
    assert "Corrupt context JSON" in caplog.text

    await q.close()


async def test_sqlite_read_result_with_corrupt_result_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_read_result should handle corrupt result JSON gracefully."""
    q = SQLiteTaskQueue(":memory:")
    await q.initialize()

    task_id = await q.enqueue("agent-a", "fast", _make_ctx())
    record = await q.claim("local")
    assert record is not None

    # Complete normally, then corrupt the stored result.
    result = AgentResult(success=True, output="ok", trace_id="t-1", run_id="r-1")
    await q.complete(task_id, result)

    assert q._db is not None
    await q._db.execute(
        "UPDATE tasks SET result = ? WHERE task_id = ?",
        ("not-json", task_id),
    )
    await q._db.commit()

    with caplog.at_level(logging.WARNING):
        polled = await q.poll_result(task_id, timeout=1.0)

    # Should fall through to the failure stub path.
    assert polled.success is False
    assert "Corrupt result JSON" in caplog.text

    await q.close()


# --- InMemory: patched records ---


async def test_inmemory_poll_result_still_works_normally() -> None:
    """Sanity check that InMemory queue works end-to-end."""
    q = InMemoryTaskQueue()
    ctx = _make_ctx()
    task_id = await q.enqueue("agent-a", "fast", ctx)
    record = await q.claim("local")
    assert record is not None

    result = AgentResult(success=True, output="done", trace_id="t-1", run_id="r-1")
    await q.complete(task_id, result)

    polled = await q.poll_result(task_id, timeout=1.0)
    assert polled.success is True
    assert polled.output == "done"
