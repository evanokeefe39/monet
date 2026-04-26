"""APScheduler-backed Scheduler implementation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from monet.schedule._protocol import ScheduleRecord, ScheduleStore

logger = logging.getLogger("monet.schedule")


class APSchedulerBackend:
    """Wraps APScheduler AsyncIOScheduler to implement the Scheduler protocol."""

    def __init__(self) -> None:
        self._scheduler: Any = None
        self._fire: Callable[[ScheduleRecord], Awaitable[None]] | None = None

    async def start(
        self,
        store: ScheduleStore,
        fire: Callable[[ScheduleRecord], Awaitable[None]],
    ) -> None:
        """Load enabled schedules from store and begin tick loop."""
        from apscheduler.schedulers.asyncio import (
            AsyncIOScheduler,
        )

        self._fire = fire
        self._scheduler = AsyncIOScheduler()
        self._scheduler.start()

        records = await store.list_all()
        for record in records:
            if record.get("enabled", True):
                self._add_to_scheduler(record)

        logger.info(
            "APSchedulerBackend started with %d jobs",
            len(self._scheduler.get_jobs()),
        )

    def _add_to_scheduler(self, record: ScheduleRecord) -> None:
        from apscheduler.triggers.cron import (
            CronTrigger,
        )

        schedule_id = record["schedule_id"]
        cron_parts = record["cron_expression"].split()
        if len(cron_parts) != 5:
            logger.warning(
                "Invalid cron expression for %s: %r",
                schedule_id,
                record["cron_expression"],
            )
            return

        minute, hour, day, month, day_of_week = cron_parts
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )

        async def _callback(rec: ScheduleRecord = record) -> None:
            if self._fire is not None:
                await self._fire(rec)

        self._scheduler.add_job(
            _callback,
            trigger=trigger,
            id=schedule_id,
            replace_existing=True,
        )

    async def add_job(self, record: ScheduleRecord) -> None:
        """Register a new cron job for the given record."""
        self._add_to_scheduler(record)

    async def remove_job(self, schedule_id: str) -> None:
        """Remove the job from the scheduler."""
        if self._scheduler and self._scheduler.get_job(schedule_id):
            self._scheduler.remove_job(schedule_id)

    async def pause_job(self, schedule_id: str) -> None:
        """Pause the job without removing it."""
        if self._scheduler and self._scheduler.get_job(schedule_id):
            self._scheduler.pause_job(schedule_id)

    async def resume_job(self, schedule_id: str) -> None:
        """Resume a previously paused job."""
        if self._scheduler and self._scheduler.get_job(schedule_id):
            self._scheduler.resume_job(schedule_id)

    async def shutdown(self) -> None:
        """Stop the tick loop."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
