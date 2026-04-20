"""Track C server surface: GET /api/v1/agents + POST /api/v1/agents/.../invoke.

Drives the FastAPI router through an ASGITransport, exercising the
capability index listing and direct-invocation endpoints without booting
a real Aegra server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from monet.server._capabilities import Capability, CapabilityIndex
from monet.server._routes import router

if TYPE_CHECKING:
    import pytest


def _app_with_index(cap_index: CapabilityIndex) -> FastAPI:
    """Build a minimal FastAPI app wiring the endpoints under test."""
    app = FastAPI()
    app.state.capability_index = cap_index
    app.state.queue = object()
    app.state.deployments = object()
    app.include_router(router)
    return app


async def test_list_agents_empty_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    app = _app_with_index(CapabilityIndex())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/agents",
            headers={"Authorization": "Bearer test-key"},
        )
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_agents_returns_index_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    cap_index = CapabilityIndex()
    cap_index.upsert_worker(
        "w1",
        "local",
        [
            Capability(agent_id="planner", command="fast", pool="local"),
            Capability(agent_id="writer", command="deep", pool="gpu"),
        ],
    )
    app = _app_with_index(cap_index)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/agents",
            headers={"Authorization": "Bearer test-key"},
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert {(c["agent_id"], c["command"]) for c in payload} == {
        ("planner", "fast"),
        ("writer", "deep"),
    }


async def test_list_agents_without_bearer_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    app = _app_with_index(CapabilityIndex())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents")
    assert resp.status_code in (401, 403)


async def test_invoke_agent_endpoint_calls_orchestration_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Posting to .../invoke dispatches invoke_agent and returns its result."""
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    app = _app_with_index(CapabilityIndex())

    captured: dict[str, Any] = {}

    async def _fake_invoke(
        agent_id: str,
        command: str = "fast",
        task: str = "",
        context: list[dict[str, Any]] | None = None,
        skills: list[str] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        captured.update(
            {
                "agent_id": agent_id,
                "command": command,
                "task": task,
                "context": context,
                "skills": skills,
            }
        )
        return {
            "success": True,
            "output": "Hello world",
            "signals": [],
            "artifacts": [],
        }

    monkeypatch.setattr(
        "monet.orchestration.invoke_agent",
        _fake_invoke,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents/writer/fast/invoke",
            json={"task": "Write a haiku"},
            headers={"Authorization": "Bearer test-key"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["output"] == "Hello world"
    assert captured == {
        "agent_id": "writer",
        "command": "fast",
        "task": "Write a haiku",
        "context": None,
        "skills": None,
    }


async def test_invoke_agent_endpoint_surfaces_invoke_errors_as_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``invoke_agent`` raising ValueError (unknown capability) → HTTP 400."""
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    app = _app_with_index(CapabilityIndex())

    async def _raises(*args: Any, **kwargs: Any) -> Any:
        msg = "Agent 'mystery/cmd' not found in manifest."
        raise ValueError(msg)

    monkeypatch.setattr(
        "monet.orchestration.invoke_agent",
        _raises,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents/mystery/cmd/invoke",
            json={"task": ""},
            headers={"Authorization": "Bearer test-key"},
        )
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"]
