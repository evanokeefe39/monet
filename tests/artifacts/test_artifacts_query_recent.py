"""Tests for the query_recent artifact-index surface."""

from __future__ import annotations

from monet.artifacts._memory import InMemoryArtifactClient
from monet.artifacts.prebuilt._index import SQLiteIndex
from monet.artifacts.prebuilt._metadata import ArtifactMetadata


def _meta(
    *,
    artifact_id: str,
    agent_id: str | None = None,
    created_at: str,
    tags: dict[str, object] | None = None,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=artifact_id,
        content_type="text/plain",
        content_length=1,
        summary="",
        confidence=1.0,
        completeness="complete",
        sensitivity_label="internal",
        agent_id=agent_id,
        run_id=None,
        trace_id=None,
        thread_id=None,
        tags=tags or {},
        created_at=created_at,
    )


async def _fresh_index() -> SQLiteIndex:
    idx = SQLiteIndex("sqlite+aiosqlite:///:memory:")
    await idx.initialise()
    return idx


async def test_query_recent_filters_by_agent() -> None:
    idx = await _fresh_index()
    await idx.put(
        _meta(artifact_id="a", agent_id="planner", created_at="2026-04-10T00:00:00Z")
    )
    await idx.put(
        _meta(artifact_id="b", agent_id="qa", created_at="2026-04-11T00:00:00Z")
    )
    rows = await idx.query_recent(agent_id="planner")
    assert [r["artifact_id"] for r in rows] == ["a"]


async def test_query_recent_filters_by_tag() -> None:
    idx = await _fresh_index()
    await idx.put(
        _meta(
            artifact_id="a",
            created_at="2026-04-10T00:00:00Z",
            tags={"run_summary": True},
        )
    )
    await idx.put(
        _meta(artifact_id="b", created_at="2026-04-11T00:00:00Z", tags={"other": True})
    )
    rows = await idx.query_recent(tag="run_summary")
    assert [r["artifact_id"] for r in rows] == ["a"]


async def test_query_recent_filters_by_since() -> None:
    idx = await _fresh_index()
    await idx.put(_meta(artifact_id="old", created_at="2026-04-01T00:00:00Z"))
    await idx.put(_meta(artifact_id="new", created_at="2026-04-15T00:00:00Z"))
    rows = await idx.query_recent(since="2026-04-10T00:00:00Z")
    assert [r["artifact_id"] for r in rows] == ["new"]


async def test_query_recent_orders_desc_and_limits() -> None:
    idx = await _fresh_index()
    for i in range(5):
        await idx.put(
            _meta(artifact_id=f"a{i}", created_at=f"2026-04-{10 + i:02d}T00:00:00Z")
        )
    rows = await idx.query_recent(limit=3)
    assert [r["artifact_id"] for r in rows] == ["a4", "a3", "a2"]


async def test_query_recent_combined_filters() -> None:
    idx = await _fresh_index()
    await idx.put(
        _meta(
            artifact_id="match",
            agent_id="researcher",
            created_at="2026-04-15T00:00:00Z",
            tags={"run_summary": True},
        )
    )
    await idx.put(
        _meta(
            artifact_id="wrong_agent",
            agent_id="qa",
            created_at="2026-04-15T00:00:00Z",
            tags={"run_summary": True},
        )
    )
    await idx.put(
        _meta(
            artifact_id="too_old",
            agent_id="researcher",
            created_at="2026-04-01T00:00:00Z",
            tags={"run_summary": True},
        )
    )
    rows = await idx.query_recent(
        agent_id="researcher", tag="run_summary", since="2026-04-10T00:00:00Z"
    )
    assert [r["artifact_id"] for r in rows] == ["match"]


async def test_list_in_memory_client() -> None:
    client = InMemoryArtifactClient()
    await client.write(b"x", artifact_id="qr-1", content_type="text/plain")
    pointers = await client.list()
    assert len(pointers) == 1
    assert pointers[0]["artifact_id"] == "qr-1"
