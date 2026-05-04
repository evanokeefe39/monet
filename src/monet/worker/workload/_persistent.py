"""Persistent and cloud-push workload execution.

Persistent: acquire a pre-warmed instance from the pool, submit the task,
collect the result, release the instance.

Cloud-push: dispatch to a cloud backend (Cloud Run / ECS), poll the cloud
API until the job terminates, retrieve the result artifact from the gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from monet.worker.execution._protocol import ContainerSpec, ExecutionBackend, JobStatus
from monet.worker.transport._errors import AgentError
from monet.worker.workload._collect import (
    TaskFailure,
    _build_agent_result,
    _renew_lease,
    _run_with_lease,
    _task_env,
)

if TYPE_CHECKING:
    from monet.config._pools import PoolConfig
    from monet.events import TaskRecord
    from monet.queue._interface import TaskQueue
    from monet.types import AgentResult
    from monet.worker.transport._protocol import TransportAdapter
    from monet.worker.workload._router import TaskRouter

__all__ = ["execute_cloud_push_workload", "execute_persistent_workload"]

_log = logging.getLogger("monet.worker.workload._persistent")


async def _retrieve_result_from_gateway(
    gateway_url: str,
    task_id: str,
    token: str,
) -> dict[str, Any]:
    """Fetch the ``_result`` artifact from the data plane gateway.

    The cloud-push agent writes its result to the gateway at key ``_result``
    before the job exits. This function retrieves and deserialises it.

    Raises:
        RuntimeError: If the HTTP request fails or the body is not valid JSON.
    """
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "execute_cloud_push_workload requires httpx: pip install httpx"
        ) from exc

    url = f"{gateway_url.rstrip('/')}/artifacts/{task_id}/_result"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        resp.raise_for_status()
    try:
        result: dict[str, Any] = json.loads(resp.content)
        return result
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Gateway returned non-JSON artifact for task {task_id}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Persistent workload (long-running pooled instance)
# ---------------------------------------------------------------------------


async def execute_persistent_workload(
    record: TaskRecord,
    pool_name: str,
    router: TaskRouter,
    transport_factory: TransportAdapter,
    queue: TaskQueue,
) -> AgentResult:
    """Submit a task to a pre-warmed persistent agent instance.

    Sequence: acquire idle instance -> connect transport -> submit payload ->
    collect result -> release instance.

    The instance is always released in a finally block. If the task times out
    or the agent sends an error, TaskFailure is raised after release.

    Raises:
        TaskFailure: When no idle instance is available (pool draining or all
            dead), on task timeout, or on agent-reported error.
    """
    instance = await router.acquire_idle(pool_name)
    if instance is None:
        raise TaskFailure("pool is draining or all instances dead")
    try:
        session = await transport_factory.connect(instance.endpoint)
        try:
            await session.submit({"task_id": record["task_id"], "payload": record})
            result = await _run_with_lease(
                session,
                queue,
                record["task_id"],
                router.task_timeout_s(pool_name),
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
        await router.release(pool_name, instance)


# ---------------------------------------------------------------------------
# Cloud-push workload (fire-and-forget dispatch + poll)
# ---------------------------------------------------------------------------


async def execute_cloud_push_workload(
    record: TaskRecord,
    pool: PoolConfig,
    backend: ExecutionBackend,
    queue: TaskQueue,
    gateway_url: str,
    token: str,
) -> AgentResult:
    """Dispatch a task to a cloud backend and poll until completion.

    Sequence: inject gateway env -> start cloud job -> poll status ->
    retrieve result artifact from gateway.

    Lease renewal runs concurrently with the poll loop and is cancelled in
    a finally block so the task record stays live during long cloud jobs.

    Raises:
        TaskFailure: On cloud job failure or task timeout.
        RuntimeError: On backend start failure or gateway retrieval error.
    """
    gateway_env = {
        "MONET_GATEWAY_URL": gateway_url,
        "MONET_TOKEN": token,
    }
    spec = ContainerSpec(image=pool.image)
    endpoint = await backend.start(spec, {**gateway_env, **_task_env(record)})

    lease_task = asyncio.create_task(_renew_lease(queue, record["task_id"]))
    try:
        while True:
            status = await backend.poll_status(endpoint)
            if status == JobStatus.SUCCEEDED:
                result = await _retrieve_result_from_gateway(
                    gateway_url, record["task_id"], token
                )
                return _build_agent_result(result)
            if status == JobStatus.FAILED:
                raise TaskFailure("cloud job failed")
            await asyncio.sleep(pool.poll_interval_s)
    except TimeoutError:
        await backend.kill(endpoint)
        raise TaskFailure("deadline exceeded") from None
    finally:
        lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)
