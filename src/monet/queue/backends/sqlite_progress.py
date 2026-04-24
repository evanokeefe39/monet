"""SQLite-backed ProgressWriter + ProgressReader for local dev and tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

import aiosqlite  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

from monet.queue._progress import EventType, ProgressEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS typed_progress_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT    NOT NULL,
    task_id      TEXT    NOT NULL,
    agent_id     TEXT    NOT NULL,
    event_type   TEXT    NOT NULL,
    payload      TEXT,
    trace_id     TEXT,
    timestamp_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tpe_run_event ON typed_progress_events (run_id, event_id);
"""

_TERMINAL_TYPES: frozenset[str] = frozenset(
    {EventType.RUN_COMPLETED, EventType.RUN_CANCELLED}
)


class SqliteProgressBackend:
    """Implements both ProgressWriter and ProgressReader protocols.

    Uses a single persistent aiosqlite connection so in-memory databases
    (``":memory:"``) survive across calls within the same backend instance.
    Concurrent coroutine access is serialised by aiosqlite's internal thread.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._init_lock = asyncio.Lock()
        self._conn: aiosqlite.Connection | None = None

    async def _ensure_init(self) -> aiosqlite.Connection:
        if self._conn is not None:
            return self._conn
        async with self._init_lock:
            if self._conn is not None:
                return self._conn
            conn = await aiosqlite.connect(self._db_path)
            conn.row_factory = aiosqlite.Row
            await conn.executescript(_SCHEMA)
            await conn.commit()
            self._conn = conn
        return self._conn

    async def close(self) -> None:
        """Close the underlying connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def record(self, run_id: str, event: ProgressEvent) -> int:
        """Append event; return assigned event_id (monotonic within run_id)."""
        db = await self._ensure_init()
        payload_json = (
            json.dumps(event["payload"]) if event.get("payload") is not None else None
        )
        cursor = await db.execute(
            """
            INSERT INTO typed_progress_events
                (run_id, task_id, agent_id, event_type, payload, trace_id, timestamp_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                event["task_id"],
                event["agent_id"],
                str(event["event_type"]),
                payload_json,
                event.get("trace_id"),
                event["timestamp_ms"],
            ),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)

    async def query(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[ProgressEvent]:
        """Return events for run_id with event_id > after, ordered ascending."""
        db = await self._ensure_init()
        cursor = await db.execute(
            """
            SELECT event_id, run_id, task_id, agent_id, event_type,
                   payload, trace_id, timestamp_ms
            FROM typed_progress_events
            WHERE run_id = ? AND event_id > ?
            ORDER BY event_id
            LIMIT ?
            """,
            (run_id, after, limit),
        )
        rows = await cursor.fetchall()
        return [_row_to_event(dict(row)) for row in rows]

    def stream(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[ProgressEvent]:
        """Yield events as they arrive; terminates on RUN_COMPLETED/RUN_CANCELLED."""
        return self._stream_gen(run_id, after=after)

    async def _stream_gen(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[ProgressEvent]:
        last_id = after
        while True:
            events = await self.query(run_id, after=last_id, limit=50)
            for event in events:
                yield event
                last_id = int(event["event_id"])
                if str(event["event_type"]) in _TERMINAL_TYPES:
                    return
            if not events:
                await asyncio.sleep(0.1)

    async def has_cause(self, run_id: str, cause_id: str) -> bool:
        """Return True if a HITL_CAUSE event with payload.cause_id exists."""
        db = await self._ensure_init()
        cursor = await db.execute(
            """
            SELECT 1 FROM typed_progress_events
            WHERE run_id = ?
              AND event_type = ?
              AND json_extract(payload, '$.cause_id') = ?
            LIMIT 1
            """,
            (run_id, str(EventType.HITL_CAUSE), cause_id),
        )
        row = await cursor.fetchone()
        return row is not None

    async def has_decision(self, run_id: str, cause_id: str) -> bool:
        """Return True if a HITL_DECISION event with payload.cause_id exists."""
        db = await self._ensure_init()
        cursor = await db.execute(
            """
            SELECT 1 FROM typed_progress_events
            WHERE run_id = ?
              AND event_type = ?
              AND json_extract(payload, '$.cause_id') = ?
            LIMIT 1
            """,
            (run_id, str(EventType.HITL_DECISION), cause_id),
        )
        row = await cursor.fetchone()
        return row is not None


def _row_to_event(row: dict[str, Any]) -> ProgressEvent:
    event: ProgressEvent = {
        "event_id": int(row["event_id"]),
        "run_id": row["run_id"],
        "task_id": row["task_id"],
        "agent_id": row["agent_id"],
        "event_type": EventType(row["event_type"]),
        "timestamp_ms": int(row["timestamp_ms"]),
    }
    if row.get("trace_id"):
        event["trace_id"] = row["trace_id"]
    if row.get("payload"):
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            event["payload"] = json.loads(row["payload"])
    return event
