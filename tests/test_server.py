"""Tests for the FastAPI server."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from monet._decorator import agent
from monet._registry import default_registry
from monet.server import create_app


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    with default_registry.registry_scope():
        yield


@pytest.fixture
def app() -> object:
    # Register a test agent before creating the app
    @agent(agent_id="test-server-agent")
    async def server_agent(task: str) -> str:
        return f"Server handled: {task}"

    return create_app()


@pytest.fixture
async def client(app: object) -> AsyncClient:  # type: ignore[misc]
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_invoke_agent(client: AsyncClient) -> None:
    response = await client.post(
        "/agents/test-server-agent/fast",
        json={
            "task": "Analyze something",
            "trace_id": "t-1",
            "run_id": "r-1",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "Analyze something" in data["output"]
    assert data["trace_id"] == "t-1"


async def test_invoke_agent_not_found(client: AsyncClient) -> None:
    response = await client.post(
        "/agents/nonexistent/fast",
        json={"task": "test"},
    )
    assert response.status_code == 404


async def test_invoke_validates_envelope(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/agents/test-server-agent/fast",
        json={},  # Missing required 'task' field
    )
    assert response.status_code == 422


async def test_catalogue_not_configured(
    client: AsyncClient,
) -> None:
    response = await client.get("/artifacts/some-id")
    assert response.status_code == 501
