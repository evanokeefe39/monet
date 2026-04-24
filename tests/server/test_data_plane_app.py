"""Tests for the data-plane app: event recording, query, and SSE stream."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from monet.queue._progress import EventType, ProgressEvent
from monet.queue.backends.sqlite_progress import SqliteProgressBackend
from monet.server import create_data_app

_API_KEY = "test-key"


@pytest.fixture
def backend() -> SqliteProgressBackend:
    return SqliteProgressBackend(":memory:")


@pytest.fixture
def client(
    backend: SqliteProgressBackend, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("MONET_API_KEY", _API_KEY)
    app = create_data_app(writer=backend, reader=backend)
    return TestClient(app, raise_server_exceptions=True)


def _auth(key: str = _API_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# --- event record ---


def test_record_event_returns_event_id(
    client: TestClient, backend: SqliteProgressBackend
) -> None:
    body = {
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": "agent_started",
        "timestamp_ms": int(time.time() * 1000),
    }
    resp = client.post(
        "/api/v1/runs/run-1/events",
        json=body,
        headers=_auth(),
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["event_id"] > 0


def test_record_event_requires_auth(client: TestClient) -> None:
    body = {
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": "agent_started",
        "timestamp_ms": int(time.time() * 1000),
    }
    resp = client.post("/api/v1/runs/run-1/events", json=body)
    assert resp.status_code in (401, 403)


# --- event query ---


@pytest.mark.asyncio
async def test_query_events_returns_stored(
    client: TestClient, backend: SqliteProgressBackend
) -> None:
    event: ProgressEvent = {
        "event_id": 0,
        "run_id": "run-2",
        "task_id": "task-1",
        "agent_id": "agent-1",
        "event_type": EventType.AGENT_COMPLETED,
        "timestamp_ms": int(time.time() * 1000),
    }
    await backend.record("run-2", event)

    resp = client.get("/api/v1/runs/run-2/events", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["events"][0]["event_type"] == "agent_completed"


def test_query_events_empty_run(client: TestClient) -> None:
    resp = client.get("/api/v1/runs/no-such-run/events", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


@pytest.mark.asyncio
async def test_query_events_after_cursor(
    client: TestClient, backend: SqliteProgressBackend
) -> None:
    e1: ProgressEvent = {
        "event_id": 0,
        "run_id": "run-3",
        "task_id": "t",
        "agent_id": "a",
        "event_type": EventType.AGENT_STARTED,
        "timestamp_ms": int(time.time() * 1000),
    }
    id1 = await backend.record("run-3", e1)
    e2: ProgressEvent = {**e1, "event_type": EventType.AGENT_COMPLETED}
    await backend.record("run-3", e2)

    resp = client.get(f"/api/v1/runs/run-3/events?after={id1}", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["events"][0]["event_type"] == "agent_completed"


# --- data app route coverage ---


def test_data_app_has_data_routes() -> None:
    backend = SqliteProgressBackend(":memory:")
    app = create_data_app(writer=backend, reader=backend)
    paths = {r.path for r in app.routes}
    assert "/api/v1/runs/{run_id}/events" in paths
    assert "/api/v1/runs/{run_id}/events/stream" in paths
    assert "/api/v1/health" in paths


def test_data_app_excludes_control_routes() -> None:
    backend = SqliteProgressBackend(":memory:")
    app = create_data_app(writer=backend, reader=backend)
    paths = {r.path for r in app.routes}
    assert "/api/v1/pools/{pool}/claim" not in paths
    assert "/api/v1/tasks/{task_id}/complete" not in paths


# --- 501 when no writer/reader ---


def test_record_returns_501_without_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONET_API_KEY", _API_KEY)
    from unittest.mock import MagicMock

    from monet.server import create_data_app as _cda

    writer = MagicMock()
    reader = MagicMock()
    app = _cda(writer=writer, reader=reader)
    # Remove the writer to simulate unconfigured state
    del app.state.progress_writer

    with TestClient(app) as c:
        resp = c.post(
            "/api/v1/runs/run-x/events",
            json={
                "task_id": "t",
                "agent_id": "a",
                "event_type": "status",
                "timestamp_ms": 1,
            },
            headers={"Authorization": f"Bearer {_API_KEY}"},
        )
        assert resp.status_code == 501
