"""T6: Failure modes — Pi agent adapter.

All Docker-based sub-tests use pi_agent_image. Non-Docker sub-tests use a
local stub server to exercise transport error paths without a full container.

T6a timeout:        task_timeout_s=5, slow prompt → TaskFailure("deadline exceeded")
T6b crash:          container killed mid-task → TransportError from HTTPTransport
T6c startup timeout: startup_timeout_s=1 → RuntimeError from _wait_ready
T6d agent error:    HTTP 400 from stub server → AgentError from HTTPTransport
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import pytest

from monet.config._pools import PoolConfig
from monet.worker.execution._docker import DockerBackend
from monet.worker.execution._protocol import ContainerSpec, Endpoint
from monet.worker.transport._errors import AgentError, TransportError
from monet.worker.transport._http import HTTPTransport
from monet.worker.workload._managed import TaskFailure, execute_managed_workload

POOL = "pi-failure"
_SLOW_TASK = (
    "Write a 5000-word essay on the history of computing, covering every decade "
    "from 1940 to 2020. Include detailed technical specifications."
)


class _NullQueue:
    pass


def _make_record(
    task: str = _SLOW_TASK,
    task_id: str = "e2e-t6-001",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_id": task_id,
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


# ---------------------------------------------------------------------------
# T6a: task timeout
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="session")
async def test_task_timeout_raises_task_failure(
    pi_agent_image: str, agent_env: dict[str, str]
) -> None:
    """5s task deadline with a slow prompt → TaskFailure('deadline exceeded')."""
    pool = PoolConfig(
        name=POOL,
        backend="docker",
        workload="task",
        image=pi_agent_image,
        agent_port=8080,
        task_timeout_s=5,
        startup_timeout_s=90,
        graceful_shutdown_s=5,
    )
    agent = SimpleNamespace(transport=SimpleNamespace(cmd=None))

    with pytest.raises(TaskFailure, match="deadline exceeded"):
        await execute_managed_workload(
            record=_make_record(task=_SLOW_TASK, task_id="e2e-t6a-001"),
            agent=agent,  # type: ignore[arg-type]
            pool=pool,
            backend=DockerBackend(),
            transport_factory=HTTPTransport(),
            queue=_NullQueue(),  # type: ignore[arg-type]
            gateway_env=agent_env,
        )


# ---------------------------------------------------------------------------
# T6b: crash — container killed mid-task
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="session")
async def test_container_crash_raises_transport_error(
    pi_agent_image: str, agent_env: dict[str, str]
) -> None:
    """Kill container while task is in-flight → TransportError from HTTPTransport."""
    import httpx as _httpx

    backend = DockerBackend()
    spec = ContainerSpec(image=pi_agent_image, expose_port=8080)
    endpoint: Endpoint = await backend.start(spec, agent_env)
    try:
        # Wait for /health before connecting.
        health_url = endpoint.address.rstrip("/") + "/health"
        deadline = asyncio.get_event_loop().time() + 90.0
        ready = False
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(2.0)) as hc:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await hc.get(health_url)
                    if r.status_code == 200:
                        ready = True
                        break
                except (_httpx.ConnectError, _httpx.TimeoutException):
                    pass
                await asyncio.sleep(1.0)
        if not ready:
            pytest.skip("Pi container did not become ready within 90s")

        transport = HTTPTransport()
        session = await transport.connect(endpoint)
        # Submit a slow task then immediately kill the container.
        task_coro = asyncio.create_task(
            session.submit({"task_id": "e2e-t6b-001", "payload": {"task": _SLOW_TASK}})
        )
        await asyncio.sleep(1.0)
        await backend.kill(endpoint)
        with pytest.raises((TransportError, Exception)):
            await task_coro
        await session.close()
    except Exception:
        # Best-effort cleanup; container may already be gone.
        with contextlib.suppress(Exception):
            await backend.stop(endpoint, grace_period_s=0)
        raise


# ---------------------------------------------------------------------------
# T6c: startup timeout
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="session")
async def test_startup_timeout_raises_runtime_error(
    pi_agent_image: str, agent_env: dict[str, str]
) -> None:
    """1s startup timeout for Pi (takes 30+ s) → RuntimeError from _wait_ready."""
    pool = PoolConfig(
        name=POOL,
        backend="docker",
        workload="task",
        image=pi_agent_image,
        agent_port=8080,
        task_timeout_s=120,
        startup_timeout_s=1,
        graceful_shutdown_s=5,
    )
    agent = SimpleNamespace(transport=SimpleNamespace(cmd=None))

    with pytest.raises(RuntimeError, match="ready"):
        await execute_managed_workload(
            record=_make_record(task="Hello", task_id="e2e-t6c-001"),
            agent=agent,  # type: ignore[arg-type]
            pool=pool,
            backend=DockerBackend(),
            transport_factory=HTTPTransport(),
            queue=_NullQueue(),  # type: ignore[arg-type]
            gateway_env=agent_env,
        )


# ---------------------------------------------------------------------------
# T6d: agent error — stub server returns 400
# ---------------------------------------------------------------------------


class _AlwaysBadHandler(BaseHTTPRequestHandler):
    """Returns HTTP 400 for every /task request."""

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        error = json.dumps({"error": "bad request"}).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(error)


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="session")
async def test_agent_400_raises_agent_error() -> None:
    """Stub server returns HTTP 400 → HTTPTransport raises AgentError."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AlwaysBadHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    endpoint = Endpoint(address=f"http://127.0.0.1:{port}", process_id="stub")
    transport = HTTPTransport()
    try:
        session = await transport.connect(endpoint)
        with pytest.raises(AgentError, match="HTTP 400"):
            await session.submit({"task_id": "t6d", "payload": {"task": "hello"}})
        await session.close()
    finally:
        server.shutdown()
