"""T2: Docker persistent workload + Pi — warm pool reuse.

Supervisor starts 2 containers, TaskRouter tracks idle/busy. Enqueue 3 tasks:
first 2 acquire one each, third blocks until one releases. Verifies only 2
containers started (no new container per task).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from monet.config._pools import PoolConfig
from monet.worker.execution._docker import DockerBackend
from monet.worker.transport._http import HTTPTransport
from monet.worker.workload._persistent import execute_persistent_workload
from monet.worker.workload._router import ManagedInstance, TaskRouter
from monet.worker.workload._supervisor import ContainerSupervisor

POOL = "pi-persistent"
_TASK = "Say 'hello' and nothing else."
_HEALTH_TIMEOUT_S = 90.0


class _NullQueue:
    pass


def _make_record(idx: int, task: str = _TASK) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": f"e2e-t2-{idx:03d}",
        "agent_id": "pi",
        "command": task,
        "pool": POOL,
        "context": {},
        "status": "claimed",
        "result": None,
        "created_at": "2026-01-01T00:00:00Z",
        "claimed_at": "2026-01-01T00:00:01Z",
        "completed_at": None,
    }


async def _wait_instance_healthy(instance: ManagedInstance, timeout_s: float) -> None:
    """Probe {instance.endpoint.address}/health until 200 or timeout."""
    address = instance.endpoint.address
    if not address:
        return
    health_url = address.rstrip("/") + "/health"
    deadline = asyncio.get_event_loop().time() + timeout_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise RuntimeError(
                    f"Instance {instance.endpoint.process_id[:12]} /health timeout"
                )
            try:
                resp = await client.get(health_url)
                if resp.status_code == 200:
                    return
            except (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
            ):
                pass
            await asyncio.sleep(min(1.0, remaining))


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="session")
async def test_pi_persistent_warm_pool_reuse(
    pi_agent_image: str, agent_env: dict[str, str]
) -> None:
    """3 tasks, 2 warm instances — only 2 containers start, 3rd waits for release."""
    pool_cfg = PoolConfig(
        name=POOL,
        backend="docker",
        workload="persistent",
        image=pi_agent_image,
        agent_port=8080,
        warm_pool_size=2,
        concurrency=2,
        task_timeout_s=120,
        startup_timeout_s=_HEALTH_TIMEOUT_S,
        heartbeat_interval_s=10,
        restart_policy="on_failure",
        max_restarts=3,
    )
    backend = DockerBackend()
    supervisor = ContainerSupervisor()
    router = TaskRouter({POOL: pool_cfg})
    transport = HTTPTransport()
    queue = _NullQueue()

    # Start warm pool — 2 containers.
    instances = await supervisor.start_pool(POOL, pool_cfg, backend, agent_env)
    assert len(instances) == 2, f"expected 2 warm instances, got {len(instances)}"

    # Wait for all instances to become healthy before registering.
    await asyncio.gather(
        *(_wait_instance_healthy(inst, _HEALTH_TIMEOUT_S) for inst in instances)
    )
    for inst in instances:
        router.add_instance(POOL, inst)

    # Dispatch 3 tasks concurrently; 3rd waits for a slot.
    results = await asyncio.gather(
        *[
            execute_persistent_workload(
                record=_make_record(i),  # type: ignore[arg-type]
                pool_name=POOL,
                router=router,
                transport_factory=transport,
                queue=queue,  # type: ignore[arg-type]
            )
            for i in range(3)
        ]
    )

    assert len(results) == 3
    for r in results:
        assert r.output is not None

    # Only 2 containers should exist (warm pool — no per-task start).
    assert len(router.get_instances(POOL)) == 2

    await supervisor.drain(POOL, router)
