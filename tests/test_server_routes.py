"""Tests for the monet orchestration server routes."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_ctx

API_KEY = "test-secret-key"


@pytest.fixture
async def app() -> Any:
    """Create a test application with an in-memory queue."""
    from monet.queue import InMemoryTaskQueue
    from monet.server import create_app

    application = create_app(queue=InMemoryTaskQueue())
    # Manually trigger lifespan so DeploymentStore is initialized
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: Any) -> Any:
    """Provide an async HTTP client bound to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


# -- Health ----------------------------------------------------------------


async def test_health_no_auth(client: AsyncClient) -> None:
    """GET /api/v1/health succeeds without authentication."""
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "workers" in data
    assert "queued" in data


# -- Worker registration ---------------------------------------------------


async def test_register_worker(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/v1/worker/register with valid auth returns deployment_id."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    resp = await client.post(
        "/api/v1/worker/register",
        json={
            "pool": "default",
            "capabilities": [
                {
                    "agent_id": "test-agent",
                    "command": "run",
                    "description": "A test agent",
                }
            ],
            "worker_id": "worker-1",
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert "deployment_id" in resp.json()


async def test_register_worker_no_auth(client: AsyncClient) -> None:
    """POST /api/v1/worker/register without auth is rejected."""
    resp = await client.post(
        "/api/v1/worker/register",
        json={
            "pool": "default",
            "capabilities": [],
            "worker_id": "worker-1",
        },
    )
    # HTTPBearer returns 403 (missing) or 401 (invalid) depending
    # on FastAPI version; either is acceptable for "not authorized".
    assert resp.status_code in (401, 403)


# -- Heartbeat -------------------------------------------------------------


async def test_heartbeat(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Register then heartbeat returns 200."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    headers = _auth_headers()

    # Register first
    await client.post(
        "/api/v1/worker/register",
        json={
            "pool": "default",
            "capabilities": [],
            "worker_id": "worker-1",
        },
        headers=headers,
    )

    resp = await client.post(
        "/api/v1/worker/heartbeat",
        json={"worker_id": "worker-1", "pool": "default"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# -- Task claiming ---------------------------------------------------------


async def test_claim_empty_pool(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/v1/tasks/claim/default on empty pool returns 204."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    resp = await client.get("/api/v1/tasks/claim/default", headers=_auth_headers())
    assert resp.status_code == 204


# -- Deployments -----------------------------------------------------------


async def test_create_deployment(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/v1/deployments creates a deployment."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    resp = await client.post(
        "/api/v1/deployments",
        json={
            "pool": "gpu",
            "capabilities": [
                {
                    "agent_id": "image-gen",
                    "command": "generate",
                    "description": "Image generator",
                }
            ],
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 201
    assert "deployment_id" in resp.json()


async def test_list_deployments(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST a deployment then GET /api/v1/deployments includes it."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    headers = _auth_headers()

    create_resp = await client.post(
        "/api/v1/deployments",
        json={"pool": "cpu", "capabilities": []},
        headers=headers,
    )
    deployment_id = create_resp.json()["deployment_id"]

    list_resp = await client.get("/api/v1/deployments", headers=headers)
    assert list_resp.status_code == 200
    ids = [d["deployment_id"] for d in list_resp.json()]
    assert deployment_id in ids


# -- Task complete / fail --------------------------------------------------


async def test_complete_task(
    app: Any,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enqueue a task, claim it, then POST complete returns 200."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    headers = _auth_headers()
    queue = app.state.queue

    ctx = make_ctx(agent_id="a1", command="run")
    task_id = await queue.enqueue("a1", "run", ctx, pool="test")

    claim_resp = await client.get("/api/v1/tasks/claim/test", headers=headers)
    assert claim_resp.status_code == 200
    claimed = claim_resp.json()
    assert claimed["task_id"] == task_id

    complete_resp = await client.post(
        f"/api/v1/tasks/{task_id}/complete",
        json={
            "success": True,
            "output": "done",
            "trace_id": "t1",
            "run_id": "r1",
        },
        headers=headers,
    )
    assert complete_resp.status_code == 200
    assert complete_resp.json()["status"] == "ok"


async def test_fail_task(
    app: Any,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enqueue a task, claim it, then POST fail returns 200."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    headers = _auth_headers()
    queue = app.state.queue

    ctx = make_ctx(agent_id="a2", command="run")
    task_id = await queue.enqueue("a2", "run", ctx, pool="fail-pool")

    await client.get("/api/v1/tasks/claim/fail-pool", headers=headers)

    fail_resp = await client.post(
        f"/api/v1/tasks/{task_id}/fail",
        json={"error": "something went wrong"},
        headers=headers,
    )
    assert fail_resp.status_code == 200
    assert fail_resp.json()["status"] == "ok"
