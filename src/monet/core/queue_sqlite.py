"""SQLite-backed persistent task queue with lease-based claiming.

Uses aiosqlite for async database access and asyncio.Event for
in-process completion notification. Claimed tasks whose leases expire
are automatically requeued by a background sweeper task.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiosqlite  # type: ignore[import-not-found]

from monet.queue import TaskStatus
from monet.signals import SignalType
from monet.types import AgentResult, ArtifactPointer, Signal

if TYPE_CHECKING:
    from pathlib import Path

    from monet.queue import TaskRecord
    from monet.types import AgentRunContext

__all__ = ["SQLiteTaskQueue"]

# Default lease TTL in seconds.
_DEFAULT_LEASE_TTL = 300

# How often the sweeper checks for expired leases.
_SWEEPER_INTERVAL = 30.0

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS tasks (
    task_id         TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    command         TEXT NOT NULL,
    pool            TEXT NOT NULL,
    context         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    result          TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    claimed_at      TEXT,
    completed_at    TEXT,
    lease_expires_at TEXT
)
"""

_CREATE_INDEX = """\
CREATE INDEX IF NOT EXISTS idx_pool_status ON tasks (pool, status)
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _serialize_result(r: AgentResult) -> str:
    return json.dumps(
        {
            "success": r.success,
            "output": r.output,
            "artifacts": [dict(a) for a in r.artifacts],
            "signals": [dict(s) for s in r.signals],
            "trace_id": r.trace_id,
            "run_id": r.run_id,
        }
    )


def _deserialize_result(raw: str) -> AgentResult:
    d: dict[str, Any] = json.loads(raw)
    return AgentResult(
        success=d["success"],
        output=d["output"],
        artifacts=tuple(
            ArtifactPointer(artifact_id=a["artifact_id"], url=a["url"])
            for a in d.get("artifacts", ())
        ),
        signals=tuple(
            Signal(type=s["type"], reason=s["reason"], metadata=s.get("metadata"))
            for s in d.get("signals", ())
        ),
        trace_id=d.get("trace_id", ""),
        run_id=d.get("run_id", ""),
    )


def _row_to_record(row: aiosqlite.Row) -> TaskRecord:
    """Convert a database row to a TaskRecord TypedDict."""
    return {
        "task_id": row[0],
        "agent_id": row[1],
        "command": row[2],
        "pool": row[3],
        "context": json.loads(row[4]),
        "status": TaskStatus(row[5]),
        "result": _deserialize_result(row[6]) if row[6] else None,
        "created_at": row[8],
        "claimed_at": row[9],
        "completed_at": row[10],
    }


class SQLiteTaskQueue:
    """Persistent task queue backed by SQLite.

    Supports lease-based claiming for crash recovery: claimed tasks
    whose leases expire are automatically requeued by a background
    sweeper task.

    Args:
        db_path: Path to the SQLite database file, or ``":memory:"``
            for an ephemeral in-memory database.
        lease_ttl: Seconds before a claimed task's lease expires and
            the sweeper requeues it.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        lease_ttl: int = _DEFAULT_LEASE_TTL,
    ) -> None:
        self._db_path = str(db_path)
        self._lease_ttl = lease_ttl
        self._db: aiosqlite.Connection | None = None
        self._events: dict[str, asyncio.Event] = {}
        self._initialized = False
        self._sweeper_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Create the database connection and schema.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if self._initialized:
            return
        self._db = await aiosqlite.connect(self._db_path)
        # Enable WAL mode for better concurrent read performance.
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_TABLE)
        await self._db.execute(_CREATE_INDEX)
        await self._db.commit()
        self._initialized = True

    async def _ensure_init(self) -> aiosqlite.Connection:
        if not self._initialized:
            await self.initialize()
        assert self._db is not None
        return self._db

    async def close(self) -> None:
        """Close the database connection and stop the sweeper."""
        self.stop_sweeper()
        if self._db is not None:
            await self._db.close()
            self._db = None
            self._initialized = False

    # --- Producer API ---

    async def enqueue(
        self,
        agent_id: str,
        command: str,
        ctx: AgentRunContext,
        pool: str = "local",
    ) -> str:
        """Submit a task to the queue.

        Args:
            agent_id: Target agent identifier.
            command: Agent command to invoke.
            ctx: Full agent run context.
            pool: Pool this task belongs to.

        Returns:
            task_id that can be passed to ``poll_result``.
        """
        db = await self._ensure_init()
        task_id = str(uuid.uuid4())
        now = _now_iso()
        await db.execute(
            "INSERT INTO tasks"
            " (task_id, agent_id, command, pool, context, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                agent_id,
                command,
                pool,
                json.dumps(ctx),
                TaskStatus.PENDING,
                now,
            ),
        )
        await db.commit()
        self._events[task_id] = asyncio.Event()
        return task_id

    async def poll_result(self, task_id: str, timeout: float) -> AgentResult:
        """Block until the task is completed or failed.

        Raises:
            TimeoutError: if ``timeout`` seconds elapse without a result.
            KeyError: if ``task_id`` is unknown.
        """
        db = await self._ensure_init()

        # Verify task exists.
        async with db.execute(
            "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)

        # If already terminal, read result immediately.
        if row[0] in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return await self._read_result(db, task_id)

        # Wait for in-process notification.
        event = self._events.get(task_id)
        if event is None:
            event = asyncio.Event()
            self._events[task_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            msg = f"Task {task_id} did not complete within {timeout}s"
            raise TimeoutError(msg) from None

        return await self._read_result(db, task_id)

    async def _read_result(self, db: aiosqlite.Connection, task_id: str) -> AgentResult:
        """Read and return the result for a terminal task."""
        async with db.execute(
            "SELECT result, context, status FROM tasks WHERE task_id = ?",
            (task_id,),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None

        # Cleanup event.
        self._events.pop(task_id, None)

        result_raw, ctx_raw, _status = row
        if result_raw:
            return _deserialize_result(result_raw)

        # Task failed/cancelled without a result object.
        ctx: AgentRunContext = json.loads(ctx_raw)
        return AgentResult(
            success=False,
            output="",
            signals=(
                Signal(
                    type=SignalType.SEMANTIC_ERROR,
                    reason="Task failed in queue",
                    metadata=None,
                ),
            ),
            trace_id=ctx["trace_id"],
            run_id=ctx["run_id"],
        )

    # --- Consumer API ---

    async def claim(self, pool: str) -> TaskRecord | None:
        """Claim the next pending task in the given pool.

        Uses an atomic UPDATE ... RETURNING to avoid races between
        concurrent workers.

        Returns:
            A TaskRecord with status CLAIMED, or None if nothing available.
        """
        db = await self._ensure_init()
        now = _now_iso()
        lease_expires = datetime.now(UTC).timestamp() + self._lease_ttl
        lease_expires_iso = datetime.fromtimestamp(lease_expires, tz=UTC).isoformat()

        # SQLite supports UPDATE ... RETURNING since 3.35 (2021-03).
        # Use a subquery to atomically select and claim.
        async with db.execute(
            "UPDATE tasks "
            "SET status = ?, claimed_at = ?, lease_expires_at = ? "
            "WHERE task_id = ("
            "  SELECT task_id FROM tasks "
            "  WHERE pool = ? AND status = ? "
            "  ORDER BY created_at LIMIT 1"
            ") RETURNING task_id, agent_id, command, pool, context, status, "
            "result, error, created_at, claimed_at, completed_at",
            (TaskStatus.CLAIMED, now, lease_expires_iso, pool, TaskStatus.PENDING),
        ) as cursor:
            row = await cursor.fetchone()
        await db.commit()

        if row is None:
            return None

        return _row_to_record(row)

    async def complete(self, task_id: str, result: AgentResult) -> None:
        """Post a successful result for a claimed task."""
        db = await self._ensure_init()
        now = _now_iso()
        async with db.execute(
            "UPDATE tasks SET status = ?, result = ?, completed_at = ? "
            "WHERE task_id = ?",
            (TaskStatus.COMPLETED, _serialize_result(result), now, task_id),
        ) as cursor:
            if cursor.rowcount == 0:
                msg = f"Unknown task_id: {task_id}"
                raise KeyError(msg)
        await db.commit()
        event = self._events.get(task_id)
        if event:
            event.set()

    async def fail(self, task_id: str, error: str) -> None:
        """Post a failure for a claimed task."""
        db = await self._ensure_init()

        # Read context for the error result.
        async with db.execute(
            "SELECT context FROM tasks WHERE task_id = ?", (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            msg = f"Unknown task_id: {task_id}"
            raise KeyError(msg)

        ctx: AgentRunContext = json.loads(row[0])
        fail_result = AgentResult(
            success=False,
            output="",
            signals=(
                Signal(
                    type=SignalType.SEMANTIC_ERROR,
                    reason=error,
                    metadata=None,
                ),
            ),
            trace_id=ctx["trace_id"],
            run_id=ctx["run_id"],
        )
        now = _now_iso()
        await db.execute(
            "UPDATE tasks SET status = ?, result = ?, error = ?, completed_at = ? "
            "WHERE task_id = ?",
            (TaskStatus.FAILED, _serialize_result(fail_result), error, now, task_id),
        )
        await db.commit()
        event = self._events.get(task_id)
        if event:
            event.set()

    async def cancel(self, task_id: str) -> None:
        """Cancel a pending or claimed task.

        Sets status to CANCELLED and signals completion so poll_result
        unblocks. If already completed/failed/cancelled, this is a no-op.
        """
        db = await self._ensure_init()
        now = _now_iso()
        await db.execute(
            "UPDATE tasks SET status = ?, completed_at = ? "
            "WHERE task_id = ? AND status IN (?, ?)",
            (
                TaskStatus.CANCELLED,
                now,
                task_id,
                TaskStatus.PENDING,
                TaskStatus.CLAIMED,
            ),
        )
        await db.commit()
        event = self._events.get(task_id)
        if event:
            event.set()

    # --- Lease sweeper ---

    def start_sweeper(self) -> None:
        """Start the background lease-expiry sweeper task."""
        if self._sweeper_task is not None and not self._sweeper_task.done():
            return
        self._sweeper_task = asyncio.create_task(self._sweeper_loop())

    def stop_sweeper(self) -> None:
        """Cancel the background sweeper task."""
        if self._sweeper_task is not None:
            self._sweeper_task.cancel()
            self._sweeper_task = None

    async def _sweeper_loop(self) -> None:
        """Periodically requeue tasks with expired leases."""
        while True:
            await asyncio.sleep(_SWEEPER_INTERVAL)
            await self._sweep_expired_leases()

    async def _sweep_expired_leases(self) -> None:
        """Requeue claimed tasks whose lease has expired."""
        db = await self._ensure_init()
        now = _now_iso()
        await db.execute(
            "UPDATE tasks SET status = ?, claimed_at = NULL, lease_expires_at = NULL "
            "WHERE status = ? AND lease_expires_at < ?",
            (TaskStatus.PENDING, TaskStatus.CLAIMED, now),
        )
        await db.commit()
