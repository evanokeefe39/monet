"""Schedule CRUD endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from monet.server._auth import require_api_key
from monet.server.routes._common import OptScheduler, OptScheduleStore  # noqa: TC001

if TYPE_CHECKING:
    from monet.schedule._protocol import ScheduleRecord

router = APIRouter()


class CreateScheduleRequest(BaseModel):
    """Body for ``POST /api/v1/schedules``."""

    graph_id: str
    input: dict[str, Any] = {}
    cron_expression: str


class ScheduleResponse(BaseModel):
    """Wire representation of a ScheduleRecord."""

    schedule_id: str
    graph_id: str
    input: dict[str, Any]
    cron_expression: str
    enabled: bool
    created_at: str
    last_run_at: str | None = None

    @classmethod
    def from_record(cls, r: ScheduleRecord) -> ScheduleResponse:
        return cls(
            schedule_id=r["schedule_id"],
            graph_id=r["graph_id"],
            input=r["input"],
            cron_expression=r["cron_expression"],
            enabled=r["enabled"],
            created_at=r["created_at"],
            last_run_at=r.get("last_run_at"),
        )


def _require_store(store: OptScheduleStore) -> Any:
    if store is None:
        raise HTTPException(status_code=503, detail="Schedule store not configured")
    return store


def _require_scheduler(scheduler: OptScheduler) -> Any:
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not configured")
    return scheduler


@router.post(
    "/schedules",
    status_code=201,
    dependencies=[Depends(require_api_key)],
)
async def create_schedule(
    body: CreateScheduleRequest,
    store: OptScheduleStore,
    scheduler: OptScheduler,
) -> ScheduleResponse:
    """Create a schedule and register the cron job."""
    _require_store(store)
    _require_scheduler(scheduler)
    assert store is not None
    assert scheduler is not None

    schedule_id = await store.create(body.graph_id, body.input, body.cron_expression)
    record = await store.get(schedule_id)
    if record is None:
        raise HTTPException(
            status_code=500, detail="Failed to retrieve created schedule"
        )
    await scheduler.add_job(record)
    return ScheduleResponse.from_record(record)


@router.get(
    "/schedules",
    dependencies=[Depends(require_api_key)],
)
async def list_schedules(store: OptScheduleStore) -> list[ScheduleResponse]:
    """List all schedules."""
    _require_store(store)
    assert store is not None
    records = await store.list_all()
    return [ScheduleResponse.from_record(r) for r in records]


@router.get(
    "/schedules/{schedule_id}",
    dependencies=[Depends(require_api_key)],
)
async def get_schedule(schedule_id: str, store: OptScheduleStore) -> ScheduleResponse:
    """Get one schedule by ID."""
    _require_store(store)
    assert store is not None
    record = await store.get(schedule_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return ScheduleResponse.from_record(record)


@router.delete(
    "/schedules/{schedule_id}",
    status_code=204,
    dependencies=[Depends(require_api_key)],
)
async def delete_schedule(
    schedule_id: str,
    store: OptScheduleStore,
    scheduler: OptScheduler,
) -> None:
    """Delete a schedule and remove its cron job."""
    _require_store(store)
    _require_scheduler(scheduler)
    assert store is not None
    assert scheduler is not None
    existed = await store.delete(schedule_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await scheduler.remove_job(schedule_id)


@router.post(
    "/schedules/{schedule_id}/enable",
    dependencies=[Depends(require_api_key)],
)
async def enable_schedule(
    schedule_id: str,
    store: OptScheduleStore,
    scheduler: OptScheduler,
) -> ScheduleResponse:
    """Enable a schedule and resume its cron job."""
    _require_store(store)
    _require_scheduler(scheduler)
    assert store is not None
    assert scheduler is not None
    existed = await store.set_enabled(schedule_id, enabled=True)
    if not existed:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await scheduler.resume_job(schedule_id)
    record = await store.get(schedule_id)
    assert record is not None
    return ScheduleResponse.from_record(record)


@router.post(
    "/schedules/{schedule_id}/disable",
    dependencies=[Depends(require_api_key)],
)
async def disable_schedule(
    schedule_id: str,
    store: OptScheduleStore,
    scheduler: OptScheduler,
) -> ScheduleResponse:
    """Disable a schedule and pause its cron job."""
    _require_store(store)
    _require_scheduler(scheduler)
    assert store is not None
    assert scheduler is not None
    existed = await store.set_enabled(schedule_id, enabled=False)
    if not existed:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await scheduler.pause_job(schedule_id)
    record = await store.get(schedule_id)
    assert record is not None
    return ScheduleResponse.from_record(record)
