"""Pool-scoped claim auth tests.

``POST /api/v1/pools/{pool}/claim`` rejects when ``consumer_id`` is not
a worker heartbeating for the named pool. Closes cross-pool poaching in
S3/S5 fleets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from monet.server._capabilities import Capability, CapabilityIndex
from monet.server._routes import router

if TYPE_CHECKING:
    import pytest


class _FakeQueue:
    async def claim(
        self, pool: str, consumer_id: str, block_ms: int
    ) -> dict[str, Any] | None:
        return {"task_id": "t1", "pool": pool, "consumer_id": consumer_id}


def _build_app(cap_index: CapabilityIndex) -> FastAPI:
    app = FastAPI()
    app.state.capability_index = cap_index
    app.state.queue = _FakeQueue()
    app.state.manifest = object()
    app.state.deployments = object()
    app.include_router(router)
    return app


_HEADERS = {"Authorization": "Bearer test-key"}


async def test_claim_rejects_worker_not_in_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    cap_index = CapabilityIndex()
    cap_index.upsert_worker(
        "w1", "gpu", [Capability(agent_id="a", command="run", pool="gpu")]
    )
    app = _build_app(cap_index)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/pools/cpu/claim",
            headers=_HEADERS,
            json={"consumer_id": "w1", "block_ms": 100},
        )
    assert resp.status_code == 403


async def test_claim_accepts_heartbeating_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    cap_index = CapabilityIndex()
    cap_index.upsert_worker(
        "w1", "gpu", [Capability(agent_id="a", command="run", pool="gpu")]
    )
    app = _build_app(cap_index)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/pools/gpu/claim",
            headers=_HEADERS,
            json={"consumer_id": "w1", "block_ms": 100},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "t1"
    assert body["pool"] == "gpu"


async def test_claim_rejects_unknown_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    app = _build_app(CapabilityIndex())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/pools/gpu/claim",
            headers=_HEADERS,
            json={"consumer_id": "ghost", "block_ms": 100},
        )
    assert resp.status_code == 403
