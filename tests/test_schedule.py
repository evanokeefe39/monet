"""Unit tests for schedule package — store and scheduler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monet.schedule._protocol import ScheduleRecord
from monet.schedule.backends.sqlite import SqliteScheduleStore

# ---------------------------------------------------------------------------
# SqliteScheduleStore
# ---------------------------------------------------------------------------


@pytest.fixture
async def store() -> SqliteScheduleStore:
    s = SqliteScheduleStore(db_path=":memory:")
    await s.initialize()
    return s


async def test_create_returns_id(store: SqliteScheduleStore) -> None:
    sid = await store.create("default", {}, "*/5 * * * *")
    assert isinstance(sid, str)
    assert len(sid) == 36  # UUID4


async def test_get_after_create(store: SqliteScheduleStore) -> None:
    sid = await store.create("execution", {"key": "val"}, "0 * * * *")
    record = await store.get(sid)
    assert record is not None
    assert record["graph_id"] == "execution"
    assert record["input"] == {"key": "val"}
    assert record["cron_expression"] == "0 * * * *"
    assert record["enabled"] is True
    assert record["last_run_at"] is None


async def test_list_all_empty(store: SqliteScheduleStore) -> None:
    assert await store.list_all() == []


async def test_list_all_returns_created(store: SqliteScheduleStore) -> None:
    await store.create("default", {}, "0 0 * * *")
    await store.create("chat", {}, "0 1 * * *")
    records = await store.list_all()
    assert len(records) == 2
    graph_ids = {r["graph_id"] for r in records}
    assert graph_ids == {"default", "chat"}


async def test_delete_existing(store: SqliteScheduleStore) -> None:
    sid = await store.create("default", {}, "*/5 * * * *")
    deleted = await store.delete(sid)
    assert deleted is True
    assert await store.get(sid) is None


async def test_delete_missing_returns_false(store: SqliteScheduleStore) -> None:
    assert await store.delete("nonexistent-id") is False


async def test_set_enabled_false(store: SqliteScheduleStore) -> None:
    sid = await store.create("default", {}, "*/5 * * * *")
    existed = await store.set_enabled(sid, enabled=False)
    assert existed is True
    record = await store.get(sid)
    assert record is not None
    assert record["enabled"] is False


async def test_set_enabled_missing_returns_false(store: SqliteScheduleStore) -> None:
    assert await store.set_enabled("no-such-id", enabled=True) is False


async def test_update_last_run(store: SqliteScheduleStore) -> None:
    sid = await store.create("default", {}, "*/5 * * * *")
    ts = "2026-04-26T12:00:00+00:00"
    await store.update_last_run(sid, ts)
    record = await store.get(sid)
    assert record is not None
    assert record["last_run_at"] == ts


async def test_close_idempotent(store: SqliteScheduleStore) -> None:
    await store.close()
    await store.close()  # second close must not raise


# ---------------------------------------------------------------------------
# APSchedulerBackend
# ---------------------------------------------------------------------------


@pytest.fixture
def make_record() -> ScheduleRecord:
    return ScheduleRecord(
        schedule_id="test-id",
        graph_id="default",
        input={},
        cron_expression="*/5 * * * *",
        enabled=True,
        created_at="2026-04-26T00:00:00+00:00",
        last_run_at=None,
    )


async def test_scheduler_start_adds_jobs(make_record: ScheduleRecord) -> None:
    from monet.schedule._apscheduler import APSchedulerBackend

    mock_aps = MagicMock()
    mock_aps.running = True

    async def fake_fire(r: ScheduleRecord) -> None:
        pass

    fake_store = AsyncMock()
    fake_store.list_all.return_value = [make_record]

    with (
        patch(
            "monet.schedule._apscheduler.APSchedulerBackend._add_to_scheduler"
        ) as mock_add,
        patch(
            "apscheduler.schedulers.asyncio.AsyncIOScheduler",
            return_value=mock_aps,
        ),
    ):
        backend = APSchedulerBackend()
        await backend.start(fake_store, fake_fire)
        mock_add.assert_called_once_with(make_record)


async def test_scheduler_start_skips_disabled(make_record: ScheduleRecord) -> None:
    from monet.schedule._apscheduler import APSchedulerBackend

    disabled = ScheduleRecord(**{**make_record, "enabled": False})  # type: ignore[misc]
    mock_aps = MagicMock()
    mock_aps.running = True

    fake_store = AsyncMock()
    fake_store.list_all.return_value = [disabled]

    with (
        patch(
            "monet.schedule._apscheduler.APSchedulerBackend._add_to_scheduler"
        ) as mock_add,
        patch(
            "apscheduler.schedulers.asyncio.AsyncIOScheduler",
            return_value=mock_aps,
        ),
    ):
        backend = APSchedulerBackend()
        await backend.start(fake_store, AsyncMock())
        mock_add.assert_not_called()


async def test_scheduler_shutdown(make_record: ScheduleRecord) -> None:
    from monet.schedule._apscheduler import APSchedulerBackend

    mock_aps = MagicMock()
    mock_aps.running = True

    fake_store = AsyncMock()
    fake_store.list_all.return_value = []

    with patch(
        "apscheduler.schedulers.asyncio.AsyncIOScheduler",
        return_value=mock_aps,
    ):
        backend = APSchedulerBackend()
        await backend.start(fake_store, AsyncMock())
        await backend.shutdown()
        mock_aps.shutdown.assert_called_once_with(wait=False)


async def test_scheduler_remove_job_noop_when_missing() -> None:
    from monet.schedule._apscheduler import APSchedulerBackend

    backend = APSchedulerBackend()
    # No scheduler started — must not raise
    await backend.remove_job("no-such-id")


# ---------------------------------------------------------------------------
# Enable/disable cycle via store
# ---------------------------------------------------------------------------


async def test_enable_disable_cycle(store: SqliteScheduleStore) -> None:
    sid = await store.create("default", {}, "*/5 * * * *")

    await store.set_enabled(sid, enabled=False)
    rec = await store.get(sid)
    assert rec is not None
    assert rec["enabled"] is False

    await store.set_enabled(sid, enabled=True)
    rec = await store.get(sid)
    assert rec is not None
    assert rec["enabled"] is True
