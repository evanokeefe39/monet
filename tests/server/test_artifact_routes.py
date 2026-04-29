"""Tests for artifact server routes — ArtifactQueryable dispatch."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from monet.server._auth import require_api_key
from monet.server.routes._artifacts import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[require_api_key] = lambda: None
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def app() -> FastAPI:
    return _make_app()


@pytest.fixture
async def client(app: FastAPI) -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ── list /artifacts ────────────────────────────────────────────────────


async def test_list_artifacts_dispatches_to_queryable_backend(
    client: AsyncClient,
) -> None:
    rows = [
        {
            "artifact_id": "a1",
            "key": "report",
            "content_type": "text/plain",
            "content_length": 10,
            "summary": "s",
            "created_at": "2026-01-01T00:00:00",
            "agent_id": "ag",
            "run_id": "r1",
            "thread_id": "t1",
        }
    ]

    class _FakeBackend:
        async def query(self, **kwargs: Any) -> list[dict[str, Any]]:
            return rows

    with patch(
        "monet.server.routes._artifacts.get_artifact_backend",
        return_value=_FakeBackend(),
    ):
        resp = await client.get("/api/v1/artifacts?thread_id=t1")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["artifacts"]) == 1
    assert data["artifacts"][0]["artifact_id"] == "a1"


async def test_list_artifacts_returns_empty_for_non_queryable(
    client: AsyncClient,
) -> None:
    class _NoQuery:
        async def write(self, content: bytes, **kwargs: Any) -> dict[str, Any]:
            return {}

        async def read(self, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
            return b"", {}

        async def list(self, **kwargs: Any) -> list[Any]:
            return []

    with patch(
        "monet.server.routes._artifacts.get_artifact_backend",
        return_value=_NoQuery(),
    ):
        resp = await client.get("/api/v1/artifacts?thread_id=t1")

    assert resp.status_code == 200
    assert resp.json()["artifacts"] == []


# ── count /artifacts/counts ────────────────────────────────────────────


async def test_count_uses_count_per_thread_when_available(
    client: AsyncClient,
) -> None:
    class _BackendWithCount:
        async def query(
            self, **kwargs: Any
        ) -> list[dict[str, Any]]:  # pragma: no cover
            raise AssertionError("query should not be called")

        async def count_per_thread(self, ids: list[str]) -> dict[str, int]:
            return {tid: 3 for tid in ids}

    with patch(
        "monet.server.routes._artifacts.get_artifact_backend",
        return_value=_BackendWithCount(),
    ):
        resp = await client.get("/api/v1/artifacts/counts?thread_ids=t1,t2")

    assert resp.status_code == 200
    assert resp.json() == {"t1": 3, "t2": 3}


async def test_count_derives_from_query_without_count_per_thread(
    client: AsyncClient,
) -> None:
    rows_by_thread = {
        "t1": [{"artifact_id": "a1"}, {"artifact_id": "a2"}],
        "t2": [{"artifact_id": "a3"}],
    }

    class _BackendQueryOnly:
        async def query(
            self,
            *,
            thread_id: str | None = None,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            return rows_by_thread.get(thread_id or "", [])

    with patch(
        "monet.server.routes._artifacts.get_artifact_backend",
        return_value=_BackendQueryOnly(),
    ):
        resp = await client.get("/api/v1/artifacts/counts?thread_ids=t1,t2")

    assert resp.status_code == 200
    assert resp.json() == {"t1": 2, "t2": 1}


async def test_count_returns_empty_for_non_queryable(
    client: AsyncClient,
) -> None:
    with patch(
        "monet.server.routes._artifacts.get_artifact_backend",
        return_value=object(),
    ):
        resp = await client.get("/api/v1/artifacts/counts?thread_ids=t1")

    assert resp.status_code == 200
    assert resp.json() == {}
