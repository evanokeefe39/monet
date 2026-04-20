"""HMAC-or-shared bearer auth on task-scoped endpoints.

Push workers carry ``HMAC_SHA256(MONET_API_KEY, task_id)`` in their
dispatch envelope; pull workers reuse the shared API key. The server's
``require_task_auth`` dependency must accept either.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from monet.queue import TaskRecord, TaskStatus
from monet.server._auth import task_hmac
from tests.conftest import make_ctx

API_KEY = "server-api-key"


def _make_task(pool: str = "local") -> TaskRecord:
    ctx = make_ctx(agent_id="a", command="fast")
    return {
        "schema_version": 1,
        "task_id": str(uuid.uuid4()),
        "agent_id": "a",
        "command": "fast",
        "pool": pool,
        "context": ctx,
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": datetime.now(UTC).isoformat(),
        "claimed_at": None,
        "completed_at": None,
    }


@pytest.fixture
async def app_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    from monet.queue import InMemoryTaskQueue
    from monet.server import create_app
    from monet.server._capabilities import Capability

    queue = InMemoryTaskQueue()
    app = create_app(queue=queue)
    # Pre-register workers for the pools touched by the claim-endpoint
    # tests so the pool-scoped claim auth check passes. Uses a distinct
    # worker_id per pool because a worker only heartbeats one pool.
    app.state.capability_index.upsert_worker(
        "w1",
        "claim-test",
        [Capability(agent_id="t", command="go", pool="claim-test")],
    )
    app.state.capability_index.upsert_worker(
        "w2", "empty", [Capability(agent_id="t", command="go", pool="empty")]
    )
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield queue, c


async def test_complete_accepts_shared_key(app_client: Any) -> None:
    queue, client = app_client
    task = _make_task()
    task_id = await queue.enqueue(task)
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/complete",
        json={"success": True, "trace_id": "t", "run_id": "r"},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert resp.status_code == 200


async def test_complete_accepts_per_task_hmac(app_client: Any) -> None:
    queue, client = app_client
    task = _make_task()
    task_id = await queue.enqueue(task)
    token = task_hmac(API_KEY, task_id)
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/complete",
        json={"success": True, "trace_id": "t", "run_id": "r"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


async def test_complete_rejects_wrong_token(app_client: Any) -> None:
    queue, client = app_client
    task = _make_task()
    task_id = await queue.enqueue(task)
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/complete",
        json={"success": True, "trace_id": "t", "run_id": "r"},
        headers={"Authorization": "Bearer totally-wrong"},
    )
    assert resp.status_code == 401


async def test_complete_rejects_hmac_for_different_task(app_client: Any) -> None:
    queue, client = app_client
    task = _make_task()
    task_id = await queue.enqueue(task)
    other_token = task_hmac(API_KEY, "some-other-task-id")
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/complete",
        json={"success": True, "trace_id": "t", "run_id": "r"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 401


async def test_progress_accepts_per_task_hmac(app_client: Any) -> None:
    queue, client = app_client
    task = _make_task()
    task_id = await queue.enqueue(task)
    token = task_hmac(API_KEY, task_id)
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/progress",
        json={"step": "working"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202


async def test_progress_rejects_oversize_payload(app_client: Any) -> None:
    queue, client = app_client
    task = _make_task()
    task_id = await queue.enqueue(task)
    token = task_hmac(API_KEY, task_id)
    huge = "x" * 1_000_000  # > MAX_INLINE_PAYLOAD_BYTES
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/progress",
        json={"blob": huge},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 413


async def test_claim_endpoint_blocking(app_client: Any) -> None:
    queue, client = app_client
    task = _make_task(pool="claim-test")
    task_id = await queue.enqueue(task)
    resp = await client.post(
        "/api/v1/pools/claim-test/claim",
        json={"consumer_id": "w1", "block_ms": 200},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert resp.status_code == 200
    assert resp.json()["task_id"] == task_id


async def test_claim_endpoint_returns_204_on_empty(app_client: Any) -> None:
    _, client = app_client
    resp = await client.post(
        "/api/v1/pools/empty/claim",
        json={"consumer_id": "w2", "block_ms": 100},
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert resp.status_code == 204
