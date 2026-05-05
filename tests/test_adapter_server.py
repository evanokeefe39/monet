"""Server integration tests using FastAPI TestClient.

Uses type=plugin to avoid subprocess dependencies.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from monet.adapter._config import AdapterConfig
from monet.adapter._errors import AdapterError
from monet.adapter._server import create_app


def _plugin_config(plugin_path: str = "test_stub_plugin:handle_task") -> AdapterConfig:
    return AdapterConfig(name="test", type="plugin", plugin=plugin_path)


def _inject_plugin(fn: Any, module_name: str = "test_stub_plugin") -> None:
    mod = types.ModuleType(module_name)
    mod.handle_task = fn  # type: ignore[attr-defined]
    sys.modules[module_name] = mod


@pytest.fixture(autouse=True)
def _cleanup_stub() -> Any:
    yield
    sys.modules.pop("test_stub_plugin", None)


@pytest.fixture()
def happy_client() -> Any:
    _inject_plugin(lambda task_id, payload: {"output": "ok", "artifacts": {}})
    app = create_app(_plugin_config())
    with (
        patch("monet.adapter._server.wait_healthy", new=AsyncMock()),
        TestClient(app, raise_server_exceptions=True) as client,
    ):
        yield client


def test_health_ok(happy_client: TestClient) -> None:
    r = happy_client.get("/health")
    assert r.status_code in (200, 503)
    assert "ok" in r.json()


def test_task_happy_path(happy_client: TestClient) -> None:
    r = happy_client.post("/task", json={"task_id": "t1", "payload": {"task": "hello"}})
    assert r.status_code == 200
    data = r.json()
    assert data["output"] == "ok"
    assert data["success"] is True


def test_task_invalid_json() -> None:
    _inject_plugin(lambda task_id, payload: {"output": "ok", "artifacts": {}})
    app = create_app(_plugin_config())
    with (
        patch("monet.adapter._server.wait_healthy", new=AsyncMock()),
        TestClient(app) as client,
    ):
        r = client.post(
            "/task",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
    assert r.status_code == 400
    assert r.json()["error_code"] == "INVALID_REQUEST"


def test_task_agent_error() -> None:
    def fail(task_id: str, payload: dict) -> dict:
        raise AdapterError("boom", "AGENT_ERROR")

    _inject_plugin(fail)
    app = create_app(_plugin_config())
    with (
        patch("monet.adapter._server.wait_healthy", new=AsyncMock()),
        TestClient(app) as client,
    ):
        r = client.post("/task", json={"task_id": "t1", "payload": {"task": "hi"}})
    assert r.status_code == 500
    assert r.json()["error_code"] == "AGENT_ERROR"


def test_task_upstream_error_is_502() -> None:
    def fail(task_id: str, payload: dict) -> dict:
        raise AdapterError("upstream down", "UPSTREAM_ERROR")

    _inject_plugin(fail)
    app = create_app(_plugin_config())
    with (
        patch("monet.adapter._server.wait_healthy", new=AsyncMock()),
        TestClient(app) as client,
    ):
        r = client.post("/task", json={"task_id": "t1", "payload": {"task": "hi"}})
    assert r.status_code == 502
