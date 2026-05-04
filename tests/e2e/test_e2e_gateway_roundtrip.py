"""T5: Gateway data plane round-trip.

Pi-gateway adapter writes the LLM response as artifact to the embedded gateway
before returning. Worker reads the artifact back from the gateway after
task completion and verifies content matches the task output.

Validates: embedded gateway, JWT minting, artifact write from container,
artifact read from worker, task-scoped isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from monet.config._pools import PoolConfig
from monet.gateway._auth import mint_task_token
from monet.worker.execution._docker import DockerBackend
from monet.worker.transport._http import HTTPTransport
from monet.worker.workload._managed import execute_managed_workload

POOL = "pi-gateway"
_TASK = "What is 2 + 2? Reply with just the number."
_TASK_ID = "e2e-t5-001"


class _NullQueue:
    pass


def _make_record(task: str = _TASK) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_id": _TASK_ID,
        "agent_id": "pi-gateway",
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
async def test_gateway_artifact_written_and_readable(
    pi_gateway_agent_image: str,
    agent_env: dict[str, str],
    embedded_gateway: dict[str, Any],
) -> None:
    """Container writes artifact to gateway; worker reads it back post-completion."""
    gw = embedded_gateway
    signing_key: str = gw["signing_key"]
    host_url: str = gw["host_url"]
    docker_url: str = gw["docker_url"]

    token = mint_task_token(
        task_id=_TASK_ID,
        run_id="e2e-run-t5",
        pool=POOL,
        scopes=["artifacts"],
        signing_key=signing_key,
    )
    gateway_env = {
        **agent_env,
        "MONET_GATEWAY_URL": docker_url,
        "MONET_TOKEN": token,
    }

    pool = PoolConfig(
        name=POOL,
        backend="docker",
        workload="task",
        image=pi_gateway_agent_image,
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
        gateway_env=gateway_env,
    )

    assert result.output is not None and len(result.output.strip()) > 0

    # Verify artifact was written to gateway and is readable.
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{host_url}/artifacts/{_TASK_ID}/research_output",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
    assert resp.status_code == 200
    artifact_text = resp.content.decode()
    assert len(artifact_text.strip()) > 0
    # Artifact content should match the task output.
    assert artifact_text.strip() == result.output.strip()
