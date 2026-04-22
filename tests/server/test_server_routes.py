"""Tests for the monet orchestration server routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from monet.queue import TaskRecord, TaskStatus
from tests.conftest import make_ctx

API_KEY = "test-secret-key"


def _make_task(agent_id: str, command: str, pool: str) -> TaskRecord:
    ctx = make_ctx(agent_id=agent_id, command=command)
    return {
        "schema_version": 1,
        "task_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "command": command,
        "pool": pool,
        "context": ctx,
        "status": TaskStatus.PENDING,
        "result": None,
        "created_at": datetime.now(UTC).isoformat(),
        "claimed_at": None,
        "completed_at": None,
    }


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
    """First heartbeat from a new worker_id registers and returns 200."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    resp = await client.post(
        "/api/v1/workers/worker-1/heartbeat",
        json={
            "pool": "default",
            "capabilities": [
                {
                    "agent_id": "test-agent",
                    "command": "run",
                    "pool": "default",
                    "description": "A test agent",
                }
            ],
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["worker_id"] == "worker-1"
    assert body["registered"] is True


async def test_register_worker_no_auth(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unified heartbeat endpoint requires auth when a key is configured."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    resp = await client.post(
        "/api/v1/workers/worker-1/heartbeat",
        json={"pool": "default", "capabilities": []},
        # no Authorization header
    )
    assert resp.status_code == 401


# -- Heartbeat -------------------------------------------------------------


async def test_heartbeat(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Second heartbeat from a known worker returns registered=False."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    headers = _auth_headers()
    body = {
        "pool": "default",
        "capabilities": [{"agent_id": "a", "command": "run", "pool": "default"}],
    }
    await client.post("/api/v1/workers/worker-1/heartbeat", json=body, headers=headers)
    resp = await client.post(
        "/api/v1/workers/worker-1/heartbeat", json=body, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["registered"] is False


# -- Task claiming ---------------------------------------------------------


async def test_claim_empty_pool(
    app: Any, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/v1/pools/default/claim on empty pool returns 204."""
    from monet.server._capabilities import Capability

    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    app.state.capability_index.upsert_worker(
        "w1",
        "default",
        [Capability(agent_id="a", command="run", pool="default")],
    )
    resp = await client.post(
        "/api/v1/pools/default/claim",
        json={"consumer_id": "w1", "block_ms": 100},
        headers=_auth_headers(),
    )
    assert resp.status_code == 204


# -- Deployments -----------------------------------------------------------


async def test_list_deployments_after_heartbeat(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker's first heartbeat seeds a deployment record listable via GET."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    headers = _auth_headers()

    await client.post(
        "/api/v1/workers/worker-gpu/heartbeat",
        json={
            "pool": "gpu",
            "capabilities": [
                {"agent_id": "image-gen", "command": "generate", "pool": "gpu"}
            ],
        },
        headers=headers,
    )

    list_resp = await client.get("/api/v1/deployments", headers=headers)
    assert list_resp.status_code == 200
    pools = [d["pool"] for d in list_resp.json()]
    assert "gpu" in pools


# -- Task complete / fail --------------------------------------------------


async def test_complete_task(
    app: Any,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enqueue a task, claim it via POST, then POST complete returns 200."""
    from monet.server._capabilities import Capability

    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    headers = _auth_headers()
    queue = app.state.queue
    app.state.capability_index.upsert_worker(
        "w1", "test", [Capability(agent_id="a1", command="run", pool="test")]
    )

    task_id = await queue.enqueue(_make_task("a1", "run", "test"))

    claim_resp = await client.post(
        "/api/v1/pools/test/claim",
        json={"consumer_id": "w1", "block_ms": 200},
        headers=headers,
    )
    assert claim_resp.status_code == 200
    assert claim_resp.json()["task_id"] == task_id

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
    """Enqueue a task, claim it via POST, then POST fail returns 200."""
    from monet.server._capabilities import Capability

    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    headers = _auth_headers()
    queue = app.state.queue
    app.state.capability_index.upsert_worker(
        "w1",
        "fail-pool",
        [Capability(agent_id="a2", command="run", pool="fail-pool")],
    )

    task_id = await queue.enqueue(_make_task("a2", "run", "fail-pool"))

    await client.post(
        "/api/v1/pools/fail-pool/claim",
        json={"consumer_id": "w1", "block_ms": 200},
        headers=headers,
    )

    fail_resp = await client.post(
        f"/api/v1/tasks/{task_id}/fail",
        json={"error": "something went wrong"},
        headers=headers,
    )
    assert fail_resp.status_code == 200
    assert fail_resp.json()["status"] == "ok"


# --- Progress history endpoint ---------------------------------------------


async def test_get_run_progress_returns_events(
    app: Any,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/runs/{run_id}/progress returns persisted events."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    queue = app.state.queue
    run_id = "test-run-1"

    await queue.publish_progress(run_id, {"agent": "writer", "status": "running"})
    await queue.publish_progress(run_id, {"agent": "writer", "status": "done"})

    resp = await client.get(
        f"/api/v1/runs/{run_id}/progress",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id
    assert len(data["events"]) == 2
    assert data["events"][0]["agent"] == "writer"


async def test_get_run_progress_empty_for_unknown_run(
    app: Any,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/runs/{run_id}/progress returns empty list for unknown run."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    resp = await client.get(
        "/api/v1/runs/nonexistent/progress",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["events"] == []


async def test_get_run_progress_501_without_progress_store(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/runs/{run_id}/progress returns 501 for non-ProgressStore backends."""

    monkeypatch.setenv("MONET_API_KEY", API_KEY)

    class BareQueue:
        """Queue that does not implement ProgressStore."""

        async def enqueue(self, task: Any) -> str:
            return ""

        async def claim(self, pool: str, consumer_id: str, block_ms: int) -> None:
            return None

        async def complete(self, task_id: str, result: Any) -> None:
            pass

        async def fail(self, task_id: str, error: str) -> None:
            pass

        async def publish_progress(self, task_id: str, event: Any) -> None:
            pass

        def subscribe_progress(self, task_id: str) -> Any:
            raise NotImplementedError

        async def await_completion(self, task_id: str, timeout: float) -> Any:
            raise TimeoutError

        async def ping(self) -> bool:
            return True

        @property
        def backend_name(self) -> str:
            return "bare"

        async def close(self) -> None:
            pass

    from monet.server import create_app

    bare_app = create_app(queue=BareQueue())  # type: ignore[arg-type]
    async with bare_app.router.lifespan_context(bare_app):
        transport = ASGITransport(app=bare_app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as bare_client:
            resp = await bare_client.get(
                "/api/v1/runs/any/progress",
                headers=_auth_headers(),
            )
            assert resp.status_code == 501


# --- Batch progress endpoint ----------------------------------------------


async def test_get_batch_progress_returns_events(
    app: Any,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/progress?run_ids=... returns events grouped by run_id."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)
    queue = app.state.queue

    await queue.publish_progress("run-a", {"agent": "w1", "status": "step1"})
    await queue.publish_progress("run-b", {"agent": "w2", "status": "step2"})

    resp = await client.get(
        "/api/v1/progress",
        params={"run_ids": "run-a,run-b"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "progress" in data
    assert len(data["progress"]["run-a"]) == 1
    assert len(data["progress"]["run-b"]) == 1
    assert data["progress"]["run-a"][0]["agent"] == "w1"


async def test_get_batch_progress_omits_empty_runs(
    app: Any,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/progress omits runs with no events from the result."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)

    resp = await client.get(
        "/api/v1/progress",
        params={"run_ids": "nonexistent-run"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["progress"] == {}


async def test_get_batch_progress_501_without_progress_store(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/progress returns 501 for non-ProgressStore backends."""
    monkeypatch.setenv("MONET_API_KEY", API_KEY)

    class BareQueue:
        async def enqueue(self, task: Any) -> str:
            return ""

        async def claim(self, pool: str, consumer_id: str, block_ms: int) -> None:
            return None

        async def complete(self, task_id: str, result: Any) -> None:
            pass

        async def fail(self, task_id: str, error: str) -> None:
            pass

        async def publish_progress(self, task_id: str, event: Any) -> None:
            pass

        def subscribe_progress(self, task_id: str) -> Any:
            raise NotImplementedError

        async def await_completion(self, task_id: str, timeout: float) -> Any:
            raise TimeoutError

        async def ping(self) -> bool:
            return True

        @property
        def backend_name(self) -> str:
            return "bare"

        async def close(self) -> None:
            pass

    from monet.server import create_app

    bare_app = create_app(queue=BareQueue())  # type: ignore[arg-type]
    async with bare_app.router.lifespan_context(bare_app):
        transport = ASGITransport(app=bare_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/progress",
                params={"run_ids": "any"},
                headers=_auth_headers(),
            )
            assert resp.status_code == 501


# --- WorkBrief DAG render -------------------------------------------------


def test_work_brief_dag_renders_mermaid_with_edges() -> None:
    from monet.server._routes import _render_work_brief_dag

    nodes = [
        {
            "id": "a",
            "agent_id": "planner",
            "command": "fast",
            "task": "draft outline",
            "depends_on": [],
        },
        {
            "id": "b",
            "agent_id": "writer",
            "command": "deep",
            "task": "write body",
            "depends_on": ["a"],
        },
        {
            "id": "c",
            "agent_id": "qa",
            "command": "check",
            "task": "review output",
            "depends_on": ["a", "b"],
        },
    ]
    html = _render_work_brief_dag(nodes)
    assert 'class="mermaid"' in html
    # Vertical (TB) so long plans scroll top-to-bottom.
    assert "graph TB" in html
    assert "n0[" in html and "n1[" in html and "n2[" in html
    # Agent badge is on top (appears before the raw node id in each box).
    assert "planner/fast" in html
    # Agent span is coloured with the accent purple.
    assert "&lt;span style='color:#a855f7" in html
    # Task text appears inside each node box.
    assert "draft outline" in html
    assert "write body" in html
    assert "review output" in html
    # Edges use aliases, not raw ids.
    assert "n0 --&gt; n1" in html
    assert "n1 --&gt; n2" in html


def test_work_brief_dag_truncates_long_task() -> None:
    from monet.server._routes import _DAG_TASK_CHAR_BUDGET, _render_work_brief_dag

    long = "x" * (_DAG_TASK_CHAR_BUDGET + 50)
    nodes = [
        {"id": "a", "agent_id": "p", "command": "c", "task": long, "depends_on": []},
    ]
    html = _render_work_brief_dag(nodes)
    # Ellipsis appended; full string not present.
    assert "…" in html
    assert long not in html


def test_work_brief_dag_empty_when_no_nodes() -> None:
    from monet.server._routes import _render_work_brief_dag

    assert _render_work_brief_dag([]) == ""


def test_work_brief_dag_escapes_quotes_in_labels() -> None:
    from monet.server._routes import _render_work_brief_dag

    nodes = [
        {"id": 'weird"id', "agent_id": "x", "command": "y", "depends_on": []},
    ]
    html = _render_work_brief_dag(nodes)
    # User quote replaced with Mermaid escape, not raw "
    assert "weird#quot;id" in html
