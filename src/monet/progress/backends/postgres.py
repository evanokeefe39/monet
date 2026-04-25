"""PostgreSQL-backed ProgressWriter + ProgressReader for production.

Requires psycopg[binary]>=3.0. Uses a connection pool for concurrent access.
Call ``await backend.initialise()`` once at startup to ensure the schema exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from monet.events import EventType, ProgressEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = logging.getLogger("monet.progress.postgres")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS typed_progress_events (
    event_id     BIGSERIAL    PRIMARY KEY,
    run_id       TEXT         NOT NULL,
    task_id      TEXT         NOT NULL,
    agent_id     TEXT         NOT NULL,
    event_type   TEXT         NOT NULL,
    payload      JSONB,
    trace_id     TEXT,
    timestamp_ms BIGINT       NOT NULL,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_tpe_run_event"
    " ON typed_progress_events (run_id, event_id);\n"
    "CREATE INDEX IF NOT EXISTS ix_tpe_run_type"
    " ON typed_progress_events (run_id, event_type);\n"
)

_TERMINAL_TYPES: frozenset[str] = frozenset(
    {EventType.RUN_COMPLETED, EventType.RUN_CANCELLED}
)


class PostgresProgressBackend:
    """Implements both ProgressWriter and ProgressReader protocols.

    Args:
        dsn: PostgreSQL connection string (e.g. ``postgresql://...``).
        min_size: Minimum pool connections. Default 1.
        max_size: Maximum pool connections. Default 5.
        poll_interval: Seconds between DB polls in ``stream()``. Default 0.1.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        poll_interval: float = 0.1,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._poll_interval = poll_interval
        self._pool: Any = None
        self._init_lock = asyncio.Lock()

    async def initialise(self) -> None:
        """Create the schema and connection pool. Idempotent."""
        if self._pool is not None:
            return
        async with self._init_lock:
            if self._pool is not None:
                return
            from psycopg_pool import (  # type: ignore[import-not-found]
                AsyncConnectionPool,
            )

            pool = AsyncConnectionPool(
                self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                open=False,
            )
            await pool.open()
            async with pool.connection() as conn:
                await conn.execute(_CREATE_TABLE)
                await conn.execute(_CREATE_INDEXES)
            self._pool = pool

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _pool_or_raise(self) -> Any:
        if self._pool is None:
            msg = (
                "PostgresProgressBackend not initialised"
                " — call await backend.initialise()"
            )
            raise RuntimeError(msg)
        return self._pool

    async def record(self, run_id: str, event: ProgressEvent) -> int:
        """Append event; return assigned event_id (monotonic within run_id)."""
        pool = await self._pool_or_raise()
        payload = event.get("payload")
        async with pool.connection() as conn:
            row = await conn.fetchone(
                """
                INSERT INTO typed_progress_events
                    (run_id, task_id, agent_id, event_type,
                     payload, trace_id, timestamp_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING event_id
                """,
                (
                    run_id,
                    event["task_id"],
                    event["agent_id"],
                    str(event["event_type"]),
                    json.dumps(payload) if payload is not None else None,
                    event.get("trace_id"),
                    event["timestamp_ms"],
                ),
            )
        return int(row[0]) if row else 0

    async def query(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[ProgressEvent]:
        """Return events for run_id with event_id > after, ordered ascending."""
        pool = await self._pool_or_raise()
        async with pool.connection() as conn:
            rows = await conn.fetchall(
                """
                SELECT event_id, run_id, task_id, agent_id, event_type,
                       payload, trace_id, timestamp_ms
                FROM typed_progress_events
                WHERE run_id = %s AND event_id > %s
                ORDER BY event_id
                LIMIT %s
                """,
                (run_id, after, limit),
            )
        return [_row_to_event(row) for row in rows]

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
                await asyncio.sleep(self._poll_interval)

    async def has_cause(self, run_id: str, cause_id: str) -> bool:
        """Return True if a HITL_CAUSE event with payload.cause_id exists."""
        pool = await self._pool_or_raise()
        async with pool.connection() as conn:
            row = await conn.fetchone(
                """
                SELECT 1 FROM typed_progress_events
                WHERE run_id = %s
                  AND event_type = %s
                  AND payload->>'cause_id' = %s
                LIMIT 1
                """,
                (run_id, str(EventType.HITL_CAUSE), cause_id),
            )
        return row is not None

    async def has_decision(self, run_id: str, cause_id: str) -> bool:
        """Return True if a HITL_DECISION event with payload.cause_id exists."""
        pool = await self._pool_or_raise()
        async with pool.connection() as conn:
            row = await conn.fetchone(
                """
                SELECT 1 FROM typed_progress_events
                WHERE run_id = %s
                  AND event_type = %s
                  AND payload->>'cause_id' = %s
                LIMIT 1
                """,
                (run_id, str(EventType.HITL_DECISION), cause_id),
            )
        return row is not None


def _row_to_event(row: tuple[Any, ...]) -> ProgressEvent:
    (
        event_id,
        run_id,
        task_id,
        agent_id,
        event_type,
        payload,
        trace_id,
        timestamp_ms,
    ) = row
    event: ProgressEvent = {
        "event_id": int(event_id),
        "run_id": run_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "event_type": EventType(event_type),
        "timestamp_ms": int(timestamp_ms),
    }
    if trace_id:
        event["trace_id"] = trace_id
    if payload is not None:
        if isinstance(payload, str):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                event["payload"] = json.loads(payload)
        elif isinstance(payload, dict):
            event["payload"] = payload
    return event
