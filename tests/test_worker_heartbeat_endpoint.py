"""Unified worker heartbeat endpoint tests.

``POST /api/v1/workers/{worker_id}/heartbeat`` is both registration and
liveness ping. First call from a new ``worker_id`` registers; later
calls reconcile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from monet.server._capabilities import CapabilityIndex
from monet.server._deployment import DeploymentStore
from monet.server._routes import router

if TYPE_CHECKING:
    import pytest


async def _build_app() -> tuple[FastAPI, CapabilityIndex, DeploymentStore]:
    deployments = DeploymentStore()
    await deployments.initialize()
    cap_index = CapabilityIndex()
    app = FastAPI()
    app.state.capability_index = cap_index
    app.state.deployments = deployments
    app.state.queue = object()
    app.include_router(router)
    return app, cap_index, deployments


_HEADERS = {"Authorization": "Bearer test-key"}


async def test_first_heartbeat_registers_new_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    app, cap_index, deployments = await _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/workers/w1/heartbeat",
            headers=_HEADERS,
            json={
                "pool": "local",
                "capabilities": [{"agent_id": "a", "command": "run", "pool": "local"}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "worker_id": "w1",
        "known_capabilities": 1,
        "registered": True,
    }
    assert cap_index.is_available("a", "run")
    assert await deployments.worker_exists("w1")
    await deployments.close()


async def test_second_heartbeat_reconciles_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    app, cap_index, deployments = await _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/v1/workers/w1/heartbeat",
            headers=_HEADERS,
            json={
                "pool": "local",
                "capabilities": [
                    {"agent_id": "a", "command": "run", "pool": "local"},
                    {"agent_id": "b", "command": "run", "pool": "local"},
                ],
            },
        )
        resp2 = await client.post(
            "/api/v1/workers/w1/heartbeat",
            headers=_HEADERS,
            json={
                "pool": "local",
                "capabilities": [{"agent_id": "a", "command": "run", "pool": "local"}],
            },
        )
    assert resp2.json() == {
        "worker_id": "w1",
        "known_capabilities": 1,
        "registered": False,
    }
    assert cap_index.is_available("a", "run")
    assert not cap_index.is_available("b", "run")
    await deployments.close()


async def test_heartbeat_with_invalid_agent_id_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    app, _, deployments = await _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/workers/w1/heartbeat",
            headers=_HEADERS,
            json={
                "pool": "local",
                "capabilities": [{"agent_id": "", "command": "run", "pool": "local"}],
            },
        )
    assert resp.status_code == 422
    await deployments.close()


async def test_heartbeat_without_bearer_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    app, _, deployments = await _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/workers/w1/heartbeat",
            json={"pool": "local", "capabilities": []},
        )
    assert resp.status_code in (401, 403)
    await deployments.close()
