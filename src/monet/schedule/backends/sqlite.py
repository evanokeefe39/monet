"""SQLite-backed ScheduleStore for local dev."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiosqlite  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from pathlib import Path

    from monet.schedule._protocol import ScheduleRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id     TEXT PRIMARY KEY,
    graph_id        TEXT NOT NULL,
    input_json      TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    last_run_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_schedules_enabled ON schedules (enabled);
"""


class SqliteScheduleStore:
    """Implements ScheduleStore protocol backed by aiosqlite.

    Precondition: ``initialize()`` must be awaited before any other method.
    """

    def __init__(self, db_path: str | Path = ".monet/schedules.db") -> None:
        self._db_path = str(db_path)
        self._init_lock = asyncio.Lock()
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create tables if they do not exist."""
        await self._ensure_init()

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

    async def create(
        self,
        graph_id: str,
        input: dict[str, Any],
        cron_expression: str,
    ) -> str:
        """Persist a new schedule; return its schedule_id."""
        schedule_id = str(uuid.uuid4())
        created_at = datetime.now(UTC).isoformat()
        db = await self._ensure_init()
        await db.execute(
            """
            INSERT INTO schedules
                (schedule_id, graph_id, input_json,
                 cron_expression, enabled, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (schedule_id, graph_id, json.dumps(input), cron_expression, created_at),
        )
        await db.commit()
        return schedule_id

    async def list_all(self) -> list[ScheduleRecord]:
        """Return all stored schedules."""
        db = await self._ensure_init()
        cursor = await db.execute("SELECT * FROM schedules ORDER BY created_at")
        rows = await cursor.fetchall()
        return [_row_to_record(dict(row)) for row in rows]

    async def get(self, schedule_id: str) -> ScheduleRecord | None:
        """Return one schedule or None if not found."""
        db = await self._ensure_init()
        cursor = await db.execute(
            "SELECT * FROM schedules WHERE schedule_id = ?",
            (schedule_id,),
        )
        row = await cursor.fetchone()
        return _row_to_record(dict(row)) if row else None

    async def delete(self, schedule_id: str) -> bool:
        """Delete schedule; return True if it existed."""
        db = await self._ensure_init()
        cursor = await db.execute(
            "DELETE FROM schedules WHERE schedule_id = ?",
            (schedule_id,),
        )
        await db.commit()
        return bool(cursor.rowcount > 0)

    async def set_enabled(self, schedule_id: str, enabled: bool) -> bool:
        """Set enabled flag; return True if record existed."""
        db = await self._ensure_init()
        cursor = await db.execute(
            "UPDATE schedules SET enabled = ? WHERE schedule_id = ?",
            (1 if enabled else 0, schedule_id),
        )
        await db.commit()
        return bool(cursor.rowcount > 0)

    async def update_last_run(self, schedule_id: str, timestamp: str) -> None:
        """Record the ISO 8601 timestamp of the most recent fire."""
        db = await self._ensure_init()
        await db.execute(
            "UPDATE schedules SET last_run_at = ? WHERE schedule_id = ?",
            (timestamp, schedule_id),
        )
        await db.commit()


def _row_to_record(row: dict[str, Any]) -> ScheduleRecord:
    record: ScheduleRecord = {
        "schedule_id": row["schedule_id"],
        "graph_id": row["graph_id"],
        "input": json.loads(row["input_json"]),
        "cron_expression": row["cron_expression"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "last_run_at": row.get("last_run_at"),
    }
    return record
