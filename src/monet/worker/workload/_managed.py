"""Per-task backend lifecycle workload execution.

Each task starts a fresh agent process, submits the task, collects the result,
and tears down the process — regardless of success or failure (saga pattern).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from monet.worker.execution._protocol import (
    ContainerSpec,
    Endpoint,
    ExecutionBackend,
    JobStatus,
)
from monet.worker.transport._errors import AgentError
from monet.worker.workload._collect import (
    TaskFailure,
    _build_agent_result,
    _run_with_lease,
    _task_env,
)

if TYPE_CHECKING:
    from monet.config._pools import PoolConfig
    from monet.core.agent_loader import AgentEntryConfig
    from monet.events import TaskRecord
    from monet.queue._interface import TaskQueue
    from monet.types import AgentResult
    from monet.worker.transport._protocol import TransportAdapter

__all__ = ["execute_managed_workload"]

_log = logging.getLogger("monet.worker.workload._managed")


async def _wait_ready(
    backend: ExecutionBackend,
    endpoint: Endpoint,
    startup_timeout_s: float,
) -> None:
    """Poll backend until the process reports RUNNING or the timeout expires.

    Raises:
        RuntimeError: If the process exits before becoming ready, or if the
            startup timeout elapses.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + startup_timeout_s
    while True:
        status = await backend.poll_status(endpoint)
        if status == JobStatus.RUNNING:
            return
        if status in (JobStatus.FAILED, JobStatus.SUCCEEDED):
            raise RuntimeError(
                f"Agent process exited during startup (status={status.value})"
            )
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RuntimeError(
                f"Agent did not become ready within {startup_timeout_s}s"
            )
        await asyncio.sleep(min(1.0, remaining))


async def execute_managed_workload(
    record: TaskRecord,
    agent: AgentEntryConfig,
    pool: PoolConfig,
    backend: ExecutionBackend,
    transport_factory: TransportAdapter,
    queue: TaskQueue,
    gateway_env: dict[str, str],
) -> AgentResult:
    """Run one task with per-task backend lifecycle.

    Sequence: start backend -> wait ready -> connect transport ->
    submit payload -> collect result -> stop backend.

    Every step that follows a successful start is protected by a finally
    block so stop() is always called, even when the task times out or the
    agent sends an error event.

    Raises:
        TaskFailure: On task timeout or agent-reported error. Caller posts
            to queue.fail().
        RuntimeError: On backend start failure or readiness timeout. These
            are infrastructure failures, not task failures.
    """
    spec = ContainerSpec(
        image=pool.image,
        entrypoint=agent.transport.cmd,
    )
    endpoint = await backend.start(spec, {**gateway_env, **_task_env(record)})
    try:
        await _wait_ready(backend, endpoint, pool.startup_timeout_s)

        session = await transport_factory.connect(endpoint)
        try:
            await session.submit({"task_id": record["task_id"], "payload": record})
            result = await _run_with_lease(
                session, queue, record["task_id"], pool.task_timeout_s
            )
            return _build_agent_result(result)
        except TimeoutError:
            await session.cancel()
            raise TaskFailure("deadline exceeded") from None
        except AgentError as exc:
            raise TaskFailure(str(exc)) from exc
        finally:
            await session.close()
    finally:
        await backend.stop(endpoint, pool.graceful_shutdown_s)
