"""Track C server surface: GET /api/v1/agents + POST /api/v1/agents/.../invoke.

Drives the FastAPI router through an ASGITransport, exercising the
manifest listing and direct-invocation endpoints without booting a
real Aegra server. Covers:

- /agents returns declared capabilities.
- /agents lists empty when the manifest has nothing.
- /agents/{id}/{cmd}/invoke runs the capability and returns AgentResult.
- /agents/{id}/{cmd}/invoke returns 400 when the capability is absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from monet.core.manifest import AgentManifest
from monet.server._routes import router

if TYPE_CHECKING:
    import pytest


def _app_with_manifest(manifest: AgentManifest) -> FastAPI:
    """Build a minimal FastAPI app with just the routes we need + manifest state."""
    app = FastAPI()
    app.state.manifest = manifest
    app.state.queue = object()  # unused by the endpoints under test
    app.state.deployments = object()
    app.include_router(router)
    return app


async def test_list_agents_empty_manifest_returns_empty_list() -> None:
    manifest = AgentManifest()
    app = _app_with_manifest(manifest)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_agents_returns_manifest_entries() -> None:
    manifest = AgentManifest()
    manifest.declare("planner", "fast", description="Triage", pool="local")
    manifest.declare("writer", "deep", description="Deep writer", pool="gpu")
    app = _app_with_manifest(manifest)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    payload = resp.json()
    assert isinstance(payload, list)
    assert {(c["agent_id"], c["command"]) for c in payload} == {
        ("planner", "fast"),
        ("writer", "deep"),
    }


async def test_invoke_agent_endpoint_calls_orchestration_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Posting to .../invoke dispatches invoke_agent and returns its result."""
    monkeypatch.setenv("MONET_API_KEY", "test-key")
    manifest = AgentManifest()
    manifest.declare("writer", "fast", pool="local")
    app = _app_with_manifest(manifest)

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
        "monet.orchestration._invoke.invoke_agent",
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
    manifest = AgentManifest()
    app = _app_with_manifest(manifest)

    async def _raises(*args: Any, **kwargs: Any) -> Any:
        msg = "Agent 'mystery/cmd' not found in manifest."
        raise ValueError(msg)

    monkeypatch.setattr(
        "monet.orchestration._invoke.invoke_agent",
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
