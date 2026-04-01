"""Tests for the FastAPI server."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from monet._decorator import agent
from monet._registry import default_registry
from monet.catalogue._memory import InMemoryCatalogueClient
from monet.server import create_app


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    with default_registry.registry_scope():
        yield


@pytest.fixture
def app() -> object:
    @agent(agent_id="test-server-agent")
    async def server_agent(task: str) -> str:
        return f"Server handled: {task}"

    catalogue = InMemoryCatalogueClient()
    return create_app(catalogue_service=catalogue)


@pytest.fixture
async def client(app: object) -> AsyncClient:  # type: ignore[misc]
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# --- Health ---


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- Agent routes ---


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


async def test_invoke_validates_envelope(client: AsyncClient) -> None:
    response = await client.post(
        "/agents/test-server-agent/fast",
        json={},
    )
    assert response.status_code == 422


# --- Catalogue routes ---


async def test_catalogue_write_and_read(client: AsyncClient) -> None:
    """Write an artifact via POST, read it back via GET."""
    response = await client.post(
        "/artifacts",
        content=b"Hello catalogue via HTTP",
        headers={
            "content-type": "text/plain",
            "x-monet-summary": "Test write",
            "x-monet-created-by": "test-agent",
            "x-monet-trace-id": "t-http",
            "x-monet-run-id": "r-http",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "artifact_id" in data
    assert "url" in data

    # Read content back
    artifact_id = data["artifact_id"]
    read_response = await client.get(f"/artifacts/{artifact_id}")
    assert read_response.status_code == 200
    assert read_response.content == b"Hello catalogue via HTTP"


async def test_catalogue_read_metadata(client: AsyncClient) -> None:
    """Read artifact metadata via /meta endpoint."""
    write_response = await client.post(
        "/artifacts",
        content=b"Meta test",
        headers={
            "content-type": "application/json",
            "x-monet-summary": "Metadata test",
            "x-monet-created-by": "meta-agent",
        },
    )
    artifact_id = write_response.json()["artifact_id"]

    meta_response = await client.get(f"/artifacts/{artifact_id}/meta")
    assert meta_response.status_code == 200
    meta = meta_response.json()
    assert meta["content_type"] == "application/json"
    assert meta["summary"] == "Metadata test"
    assert meta["created_by"] == "meta-agent"


async def test_catalogue_read_not_found(client: AsyncClient) -> None:
    response = await client.get("/artifacts/nonexistent-id")
    assert response.status_code == 404


async def test_catalogue_not_configured() -> None:
    """Without catalogue service, routes return 501."""
    from monet.server._catalogue_routes import set_catalogue_service

    set_catalogue_service(None)  # type: ignore[arg-type]
    app = create_app()  # No catalogue
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/artifacts/some-id")
        assert response.status_code == 501
