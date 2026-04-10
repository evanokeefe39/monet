"""Deployment record storage — tracks registered agent capabilities per pool."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict

import aiosqlite

if TYPE_CHECKING:
    from monet.core.manifest import AgentCapability

__all__ = ["DeploymentRecord", "DeploymentStore"]


class DeploymentRecord(TypedDict):
    """A single deployment record tracking an agent pool registration."""

    deployment_id: str
    pool: str
    capabilities: list[AgentCapability]
    worker_id: str | None
    created_at: str
    last_heartbeat: str | None
    status: str  # "active" | "inactive"


class DeploymentStore:
    """SQLite-backed deployment record storage.

    Tracks which agents are registered in which pools and which workers
    are active. Capabilities are JSON-serialized in the database.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS deployments (
                deployment_id TEXT PRIMARY KEY,
                pool TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                worker_id TEXT,
                created_at TEXT NOT NULL,
                last_heartbeat TEXT,
                status TEXT NOT NULL DEFAULT 'active'
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_deployments_pool "
            "ON deployments(pool, status)"
        )
        await self._db.commit()

    def _require_db(self) -> aiosqlite.Connection:
        """Return the database connection or raise if not initialized."""
        if self._db is None:
            raise RuntimeError(
                "DeploymentStore not initialized — call initialize() first"
            )
        return self._db

    async def create(self, pool: str, capabilities: list[AgentCapability]) -> str:
        """Create a deployment record.

        Returns:
            The generated deployment_id.
        """
        db = self._require_db()
        deployment_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO deployments "
            "(deployment_id, pool, capabilities, created_at, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            (deployment_id, pool, json.dumps(capabilities), now),
        )
        await db.commit()
        return deployment_id

    async def register_worker(self, deployment_id: str, worker_id: str) -> None:
        """Associate a worker with a deployment and update heartbeat."""
        db = self._require_db()
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "UPDATE deployments SET worker_id = ?, last_heartbeat = ? "
            "WHERE deployment_id = ?",
            (worker_id, now, deployment_id),
        )
        await db.commit()

    async def heartbeat(self, worker_id: str) -> None:
        """Update last_heartbeat for all deployments matching worker_id."""
        db = self._require_db()
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "UPDATE deployments SET last_heartbeat = ? WHERE worker_id = ?",
            (now, worker_id),
        )
        await db.commit()

    async def update_capabilities(
        self, worker_id: str, capabilities: list[dict[str, str]]
    ) -> None:
        """Update stored capabilities for all active deployments of a worker.

        Called during heartbeat when a worker sends updated capabilities
        (e.g. after hot-reload).
        """
        db = self._require_db()
        await db.execute(
            "UPDATE deployments SET capabilities = ? "
            "WHERE worker_id = ? AND status = 'active'",
            (json.dumps(capabilities), worker_id),
        )
        await db.commit()

    async def get_active(self, pool: str | None = None) -> list[DeploymentRecord]:
        """Get active deployments, optionally filtered by pool.

        Args:
            pool: If provided, only return deployments in this pool.

        Returns:
            List of active deployment records.
        """
        db = self._require_db()
        if pool is not None:
            cursor = await db.execute(
                "SELECT deployment_id, pool, capabilities, worker_id, "
                "created_at, last_heartbeat, status "
                "FROM deployments WHERE status = 'active' AND pool = ?",
                (pool,),
            )
        else:
            cursor = await db.execute(
                "SELECT deployment_id, pool, capabilities, worker_id, "
                "created_at, last_heartbeat, status "
                "FROM deployments WHERE status = 'active'"
            )
        rows = await cursor.fetchall()
        return [
            DeploymentRecord(
                deployment_id=row[0],
                pool=row[1],
                capabilities=json.loads(row[2]),
                worker_id=row[3],
                created_at=row[4],
                last_heartbeat=row[5],
                status=row[6],
            )
            for row in rows
        ]

    async def deactivate_stale(self, timeout: int = 90) -> int:
        """Mark deployments as inactive if heartbeat is older than timeout.

        Args:
            timeout: Seconds since last heartbeat before marking inactive.

        Returns:
            Count of deployments deactivated.
        """
        db = self._require_db()
        cutoff = datetime.now(UTC).isoformat()
        # SQLite datetime comparison works on ISO 8601 strings
        cursor = await db.execute(
            "UPDATE deployments SET status = 'inactive' "
            "WHERE status = 'active' "
            "AND last_heartbeat IS NOT NULL "
            "AND julianday(?) - julianday(last_heartbeat) > ? / 86400.0",
            (cutoff, timeout),
        )
        await db.commit()
        return cursor.rowcount

    async def deactivate_stale_returning_worker_ids(
        self, timeout: int = 90
    ) -> list[str]:
        """Mark stale deployments inactive and return their worker_ids.

        Args:
            timeout: Seconds since last heartbeat before marking inactive.

        Returns:
            List of worker_ids from deactivated deployments.
        """
        db = self._require_db()
        cutoff = datetime.now(UTC).isoformat()

        # Find worker_ids that will be deactivated.
        cursor = await db.execute(
            "SELECT DISTINCT worker_id FROM deployments "
            "WHERE status = 'active' "
            "AND last_heartbeat IS NOT NULL "
            "AND worker_id IS NOT NULL "
            "AND julianday(?) - julianday(last_heartbeat) > ? / 86400.0",
            (cutoff, timeout),
        )
        rows = await cursor.fetchall()
        worker_ids = [row[0] for row in rows]

        if worker_ids:
            # Mark them inactive.
            await db.execute(
                "UPDATE deployments SET status = 'inactive' "
                "WHERE status = 'active' "
                "AND last_heartbeat IS NOT NULL "
                "AND julianday(?) - julianday(last_heartbeat) > ? / 86400.0",
                (cutoff, timeout),
            )
            await db.commit()

        return worker_ids

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
