"""Schedule protocols and record type."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from typing_extensions import TypedDict


class ScheduleRecord(TypedDict, total=False):
    """Wire-format record for a persisted schedule."""

    schedule_id: str
    graph_id: str
    input: dict[str, Any]
    cron_expression: str
    enabled: bool
    created_at: str
    last_run_at: str | None


@runtime_checkable
class ScheduleStore(Protocol):
    """Persistence for schedule records."""

    async def create(
        self,
        graph_id: str,
        input: dict[str, Any],
        cron_expression: str,
    ) -> str:
        """Persist a new schedule; return its schedule_id."""
        ...

    async def list_all(self) -> list[ScheduleRecord]:
        """Return all stored schedules."""
        ...

    async def get(self, schedule_id: str) -> ScheduleRecord | None:
        """Return one schedule or None if not found."""
        ...

    async def delete(self, schedule_id: str) -> bool:
        """Delete schedule; return True if it existed."""
        ...

    async def set_enabled(self, schedule_id: str, enabled: bool) -> bool:
        """Set enabled flag; return True if record existed."""
        ...

    async def update_last_run(self, schedule_id: str, timestamp: str) -> None:
        """Record the ISO 8601 timestamp of the most recent fire."""
        ...

    async def close(self) -> None:
        """Release any held resources."""
        ...


@runtime_checkable
class Scheduler(Protocol):
    """Scheduling engine — owns cron evaluation and job dispatch."""

    async def start(
        self,
        store: ScheduleStore,
        fire: Any,  # Callable[[ScheduleRecord], Awaitable[None]]
    ) -> None:
        """Hydrate jobs from store and begin the tick loop."""
        ...

    async def add_job(self, record: ScheduleRecord) -> None:
        """Register a new cron job for the given record."""
        ...

    async def remove_job(self, schedule_id: str) -> None:
        """Remove the job from the scheduler."""
        ...

    async def pause_job(self, schedule_id: str) -> None:
        """Pause (but do not remove) the job."""
        ...

    async def resume_job(self, schedule_id: str) -> None:
        """Resume a previously paused job."""
        ...

    async def shutdown(self) -> None:
        """Stop the tick loop and clean up."""
        ...
