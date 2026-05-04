"""T4: Mixed-pool single worker — Pi and ZeroClaw run concurrently.

Two Docker pools (Pi + ZeroClaw) execute tasks in parallel. Validates
multi-pool routing by backend, concurrent execution across pool types.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from monet.config._pools import PoolConfig
from monet.worker.execution._docker import DockerBackend
from monet.worker.transport._http import HTTPTransport
from monet.worker.workload._managed import execute_managed_workload

_PI_TASK = "What is 2 + 2? Respond with just the number."
_ZC_TASK = "Run the shell command: python3 -c 'print(2+2)' and report the output."


class _NullQueue:
    pass


def _make_record(
    idx: int, pool: str, agent_id: str, task: str = _PI_TASK
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": f"e2e-t4-{idx:03d}",
        "agent_id": agent_id,
        "command": task,
        "pool": pool,
        "context": {},
        "status": "claimed",
        "result": None,
        "created_at": "2026-01-01T00:00:00Z",
        "claimed_at": "2026-01-01T00:00:01Z",
        "completed_at": None,
    }


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="session")
async def test_pi_and_zeroclaw_run_concurrently(
    pi_agent_image: str,
    zeroclaw_agent_image: str,
    agent_env: dict[str, str],
) -> None:
    """Pi task and ZeroClaw task execute in parallel; both return output."""
    pi_pool = PoolConfig(
        name="pi-pool",
        backend="docker",
        workload="task",
        image=pi_agent_image,
        agent_port=8080,
        task_timeout_s=120,
        startup_timeout_s=90,
        graceful_shutdown_s=10,
    )
    zc_pool = PoolConfig(
        name="zeroclaw-pool",
        backend="docker",
        workload="task",
        image=zeroclaw_agent_image,
        agent_port=8080,
        task_timeout_s=180,
        startup_timeout_s=90,
        graceful_shutdown_s=10,
    )
    agent = SimpleNamespace(transport=SimpleNamespace(cmd=None))

    pi_result, zc_result = await asyncio.gather(
        execute_managed_workload(
            record=_make_record(0, "pi-pool", "pi"),  # type: ignore[arg-type]
            agent=agent,  # type: ignore[arg-type]
            pool=pi_pool,
            backend=DockerBackend(),
            transport_factory=HTTPTransport(),
            queue=_NullQueue(),  # type: ignore[arg-type]
            gateway_env=agent_env,
        ),
        execute_managed_workload(
            record=_make_record(1, "zeroclaw-pool", "zeroclaw", _ZC_TASK),  # type: ignore[arg-type]
            agent=agent,  # type: ignore[arg-type]
            pool=zc_pool,
            backend=DockerBackend(),
            transport_factory=HTTPTransport(),
            queue=_NullQueue(),  # type: ignore[arg-type]
            gateway_env=agent_env,
        ),
    )

    assert pi_result.output is not None and len(pi_result.output.strip()) > 0
    assert zc_result.output is not None and len(zc_result.output.strip()) > 0
