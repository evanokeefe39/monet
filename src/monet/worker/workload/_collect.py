"""Shared helpers for workload execution.

Provides task-result collection, lease renewal, and the utility functions
used by both managed and persistent workload paths.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from monet.queue._interface import QueueMaintenance
from monet.types import AgentResult, Signal, build_artifact_pointer
from monet.worker.transport._errors import ProtocolError

if TYPE_CHECKING:
    from monet.events import TaskRecord
    from monet.queue._interface import TaskQueue
    from monet.worker.transport._protocol import Session

__all__ = [
    "TaskFailure",
    "_build_agent_result",
    "_collect",
    "_renew_lease",
    "_run_with_lease",
    "_task_env",
]

_log = logging.getLogger("monet.worker.workload._collect")

# Renew at 1/3 of the lease TTL so there is time for two retries before expiry.
_LEASE_RENEWAL_FRACTION = 1 / 3


class TaskFailure(Exception):  # noqa: N818
    """Terminal failure for a single task execution.

    Raised by workload functions to signal the caller (claim loop) that the
    task should be posted to queue.fail(). Distinct from RuntimeError so the
    claim loop can distinguish expected agent failures from infrastructure bugs.
    """


# ---------------------------------------------------------------------------
# Environment / result helpers (shared across managed and persistent paths)
# ---------------------------------------------------------------------------


def _task_env(record: TaskRecord) -> dict[str, str]:
    """Build standard MONET_* env vars from a task record."""
    ctx: dict[str, Any] = record.get("context") or {}  # type: ignore[assignment]
    return {
        "MONET_TASK_ID": record["task_id"],
        "MONET_AGENT_ID": record["agent_id"],
        "MONET_COMMAND": record["command"],
        "MONET_POOL": record["pool"],
        "MONET_RUN_ID": ctx.get("run_id", ""),
    }


def _build_agent_result(result: dict[str, Any]) -> AgentResult:
    """Construct an AgentResult from a raw result dict."""
    artifacts = tuple(build_artifact_pointer(a) for a in result.get("artifacts", []))
    signals: tuple[Signal, ...] = tuple(
        Signal(  # type: ignore[call-arg]
            type=s.get("type", ""),
            reason=s.get("reason", ""),
            metadata=s.get("metadata"),
        )
        for s in result.get("signals", [])
    )
    return AgentResult(
        success=bool(result.get("success", False)),
        output=result.get("output"),
        artifacts=artifacts,
        signals=signals,
        trace_id=str(result.get("trace_id", "")),
        run_id=str(result.get("run_id", "")),
    )


# ---------------------------------------------------------------------------
# Lease renewal
# ---------------------------------------------------------------------------


async def _renew_lease(queue: TaskQueue, task_id: str) -> None:
    """Periodically renew a task lease for backends implementing QueueMaintenance.

    Parks forever (no-op) when the backend does not support lease renewal.
    Swallows renewal failures — a missed renewal is non-fatal; the reclaim
    sweeper tolerates one missed heartbeat before evicting a task.
    """
    if not isinstance(queue, QueueMaintenance):
        await asyncio.sleep(float("inf"))
        return
    interval = queue.lease_ttl_seconds * _LEASE_RENEWAL_FRACTION
    while True:
        await asyncio.sleep(interval)
        try:
            await queue.renew_lease(task_id)
        except Exception:
            _log.debug("Lease renewal failed for task %s", task_id, exc_info=True)


# ---------------------------------------------------------------------------
# Result collection
# ---------------------------------------------------------------------------


async def _collect(session: Session) -> dict[str, Any]:
    """Drain a transport session until a result event is received.

    Raises:
        ProtocolError: If the session ends without emitting a result event.
    """
    async for event in session.receive():
        if event.type == "result":
            return event.data
    raise ProtocolError("session ended without result event")


async def _run_with_lease(
    session: Session,
    queue: TaskQueue,
    task_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    """Collect result with concurrent lease renewal and a deadline.

    Creates a background lease-renewal task that is cancelled (and awaited)
    in a finally block regardless of how _collect exits — success, timeout,
    or exception.

    Raises:
        TimeoutError: If the timeout expires before a result arrives.
        ProtocolError: If the session ends without a result event.
        TransportError / AgentError: Propagated from _collect.
    """
    lease_task = asyncio.create_task(_renew_lease(queue, task_id))
    try:
        return await asyncio.wait_for(_collect(session), timeout=timeout_s)
    finally:
        lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)
