"""SQLite-backed progress store for persistent monolith telemetry.

Provides O(1) thread-level retrieval that survives server restarts.
Includes a retention policy (global cap + TTL) to prevent database bloat.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from monet.queue import ProgressStore

_log = logging.getLogger("monet.queue.sqlite_store")


class SqliteProgressStore(ProgressStore):
    """Persistent progress store for monolith/local dev.

    Attributes:
        db_path: Path to the SQLite database file.
        max_events: Global cap on the number of stored events.
        ttl_days: Number of days to retain events (cleaned at boot).
    """

    def __init__(
        self,
        db_path: str | Path,
        max_events: int = 50_000,
        ttl_days: int = 7,
    ) -> None:
        self.db_path = Path(db_path)
        self.max_events = max_events
        self.ttl_days = ttl_days

        self._init_db()
        self._apply_retention_policy()

    def _init_db(self) -> None:
        """Create schema and indexes if they do not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS progress_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT,
                    task_id TEXT,
                    run_id TEXT,
                    timestamp_ms INTEGER,
                    payload_json TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_thread ON progress_events(thread_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task ON progress_events(task_id)"
            )
            # Index for retention cleanup
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ts ON progress_events(timestamp_ms)"
            )

    def _apply_retention_policy(self) -> None:
        """Cleanup old or excessive records at startup."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # 1. TTL Cleanup
                cutoff = int((time.time() - (self.ttl_days * 86400)) * 1000)
                res = conn.execute(
                    "DELETE FROM progress_events WHERE timestamp_ms < ?", (cutoff,)
                )
                if res.rowcount > 0:
                    _log.info(
                        "Pruned %d expired telemetry events (TTL=%d days)",
                        res.rowcount,
                        self.ttl_days,
                    )

                # 2. Global Cap Cleanup
                cursor = conn.execute("SELECT COUNT(*) FROM progress_events")
                count = cursor.fetchone()[0]
                if count > self.max_events:
                    to_delete = count - self.max_events
                    conn.execute(
                        """
                        DELETE FROM progress_events
                        WHERE id IN (
                            SELECT id FROM progress_events
                            ORDER BY timestamp_ms ASC, id ASC
                            LIMIT ?
                        )
                        """,
                        (to_delete,),
                    )
                    _log.info(
                        "Pruned %d excessive telemetry events (Cap=%d)",
                        to_delete,
                        self.max_events,
                    )
        except Exception as exc:
            _log.warning("Telemetry retention cleanup failed: %s", exc)

    async def publish_progress(
        self, task_id: str, event: dict[str, Any], **kwargs: Any
    ) -> None:
        """Persist a progress event to SQLite."""
        thread_id = event.get("thread_id") or kwargs.get("thread_id") or ""
        run_id = event.get("run_id") or kwargs.get("run_id") or ""
        ts = event.get("timestamp_ms") or int(time.time() * 1000)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO progress_events
                    (thread_id, task_id, run_id, timestamp_ms, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(thread_id), task_id, str(run_id), int(ts), json.dumps(event)),
                )
        except Exception:
            _log.error("Failed to write telemetry to SQLite", exc_info=True)

    async def get_progress_history(
        self, run_id: str, *, count: int = 1000
    ) -> list[dict[str, Any]]:
        """Fetch history for a specific run."""
        return await self._query_events("run_id", run_id, count)

    async def get_thread_progress_history(
        self, thread_id: str, *, count: int = 1000
    ) -> list[dict[str, Any]]:
        """Fetch all history for a thread across all runs."""
        return await self._query_events("thread_id", thread_id, count)

    async def _query_events(
        self, key: str, value: str, count: int
    ) -> list[dict[str, Any]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    f"""
                    SELECT payload_json FROM progress_events
                    WHERE {key} = ?
                    ORDER BY timestamp_ms ASC, id ASC
                    LIMIT ?
                    """,
                    (value, count),
                )
                return [json.loads(row[0]) for row in cursor.fetchall()]
        except Exception:
            _log.error("Failed to query telemetry from SQLite", exc_info=True)
            return []

    async def expire_progress(self, run_id: str, ttl: int) -> None:
        """No-op: deletion managed by global boot-time policy."""
        pass
