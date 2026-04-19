"""Tests for deployment record storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from monet.server._deployment import DeploymentStore

if TYPE_CHECKING:
    from pathlib import Path


def _cap(agent_id: str, command: str = "run", pool: str = "local") -> dict[str, str]:
    """Create a minimal capability dict for testing."""
    return {
        "agent_id": agent_id,
        "command": command,
        "description": "",
        "pool": pool,
    }


@pytest.mark.asyncio
async def test_create_deployment() -> None:
    store = DeploymentStore()
    await store.initialize()
    try:
        dep_id = await store.create("local", [_cap("writer")])
        assert isinstance(dep_id, str)
        assert len(dep_id) == 36  # UUID format

        active = await store.get_active()
        assert len(active) == 1
        rec = active[0]
        assert rec["deployment_id"] == dep_id
        assert rec["pool"] == "local"
        assert rec["capabilities"] == [_cap("writer")]
        assert rec["worker_id"] is None
        assert rec["status"] == "active"
        assert rec["created_at"] is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_register_worker() -> None:
    store = DeploymentStore()
    await store.initialize()
    try:
        dep_id = await store.create("local", [_cap("researcher")])
        await store.register_worker(dep_id, "worker-1")

        active = await store.get_active()
        assert len(active) == 1
        assert active[0]["worker_id"] == "worker-1"
        assert active[0]["last_heartbeat"] is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_heartbeat() -> None:
    store = DeploymentStore()
    await store.initialize()
    try:
        dep_id = await store.create("local", [_cap("qa")])
        await store.register_worker(dep_id, "worker-2")

        active_before = await store.get_active()
        hb_before = active_before[0]["last_heartbeat"]

        await store.heartbeat("worker-2")

        active_after = await store.get_active()
        hb_after = active_after[0]["last_heartbeat"]
        assert hb_after is not None
        assert hb_after >= hb_before  # type: ignore[operator]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_active_filters_by_pool() -> None:
    store = DeploymentStore()
    await store.initialize()
    try:
        await store.create("local", [_cap("writer", pool="local")])
        await store.create("remote", [_cap("researcher", pool="remote")])
        await store.create("local", [_cap("qa", pool="local")])

        local = await store.get_active(pool="local")
        assert len(local) == 2
        assert all(r["pool"] == "local" for r in local)

        remote = await store.get_active(pool="remote")
        assert len(remote) == 1
        assert remote[0]["pool"] == "remote"

        all_active = await store.get_active()
        assert len(all_active) == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_deactivate_stale() -> None:
    store = DeploymentStore()
    await store.initialize()
    try:
        dep_id = await store.create("local", [_cap("writer")])
        await store.register_worker(dep_id, "worker-stale")

        # Manually set last_heartbeat to 2 minutes ago
        db = store._require_db()
        old_time = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        await db.execute(
            "UPDATE deployments SET last_heartbeat = ? WHERE deployment_id = ?",
            (old_time, dep_id),
        )
        await db.commit()

        count = await store.deactivate_stale(timeout=90)
        assert count == 1

        active = await store.get_active()
        assert len(active) == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_close() -> None:
    store = DeploymentStore()
    await store.initialize()
    assert store._db is not None
    await store.close()
    assert store._db is None


@pytest.mark.asyncio
async def test_persistent_db(tmp_path: Path) -> None:
    """Verify data persists across store instances with a file-backed DB."""
    db_file = str(tmp_path / "deploy.db")

    store1 = DeploymentStore(db_path=db_file)
    await store1.initialize()
    dep_id = await store1.create("local", [_cap("writer")])
    await store1.close()

    store2 = DeploymentStore(db_path=db_file)
    await store2.initialize()
    active = await store2.get_active()
    assert len(active) == 1
    assert active[0]["deployment_id"] == dep_id
    await store2.close()
