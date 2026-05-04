"""T3: Docker managed workload + ZeroClaw — third-party agent lifecycle.

Same flow as T1 with the ZeroClaw agent. Longer startup (model config load).

Validates: managed lifecycle with pre-built third-party agent, OpenAI-compatible
LLM provider path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from monet.config._pools import PoolConfig
from monet.worker.execution._docker import DockerBackend
from monet.worker.transport._http import HTTPTransport
from monet.worker.workload._managed import execute_managed_workload

POOL = "zeroclaw-managed"
_TASK = "Run the shell command: python3 -c 'print(2+2)' and report the output."


class _NullQueue:
    pass


def _make_record(task: str = _TASK) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": "e2e-t3-001",
        "agent_id": "zeroclaw",
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
async def test_zeroclaw_managed_full_lifecycle(
    zeroclaw_agent_image: str, agent_env: dict[str, str]
) -> None:
    """End-to-end task with ZeroClaw agent and NIM LLM."""
    pool = PoolConfig(
        name=POOL,
        backend="docker",
        workload="task",
        image=zeroclaw_agent_image,
        agent_port=8080,
        task_timeout_s=180,
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
