"""T1: Docker managed workload + Pi agent — core lifecycle.

Flow: DockerBackend.start → _wait_ready (container RUNNING + /health 200) →
HTTPTransport.connect → submit /task → Pi /chat → NIM LLM → result → stop.

Validates: managed lifecycle, Docker port publishing, HTTP transport,
real LLM round-trip, startup/shutdown.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from monet.config._pools import PoolConfig
from monet.worker.execution._docker import DockerBackend
from monet.worker.transport._http import HTTPTransport
from monet.worker.workload._managed import execute_managed_workload

POOL = "pi-managed"
_TASK = "What is 2 + 2? Respond with just the number."


class _NullQueue:
    """Minimal queue that disables lease renewal (no QueueMaintenance)."""


def _make_record(task: str = _TASK) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": "e2e-t1-001",
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


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="session")
async def test_pi_managed_full_lifecycle(
    pi_agent_image: str, agent_env: dict[str, str]
) -> None:
    """End-to-end task execution with real Docker container and Pi + NIM LLM."""
    pool = PoolConfig(
        name=POOL,
        backend="docker",
        workload="task",
        image=pi_agent_image,
        agent_port=8080,
        task_timeout_s=120,
        startup_timeout_s=90,
        graceful_shutdown_s=10,
    )
    agent = SimpleNamespace(transport=SimpleNamespace(cmd=None))

    result = await execute_managed_workload(
        record=_make_record(),  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
        pool=pool,
        backend=DockerBackend(),
        transport_factory=HTTPTransport(),
        queue=_NullQueue(),  # type: ignore[arg-type]
        gateway_env=agent_env,
    )

    assert result.output is not None
    assert len(result.output.strip()) > 0
