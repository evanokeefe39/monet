"""Tests for the artifact catalogue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from monet.catalogue._index import SQLiteIndex
from monet.catalogue._memory import InMemoryCatalogueClient
from monet.catalogue._service import CatalogueService
from monet.catalogue._storage import FilesystemStorage

if TYPE_CHECKING:
    from pathlib import Path


# --- InMemoryCatalogueClient ---


async def test_memory_write_read() -> None:
    client = InMemoryCatalogueClient()
    ptr = await client.write(
        b"hello",
        content_type="text/plain",
        summary="test",
        confidence=0.9,
        completeness="complete",
    )
    assert ptr["artifact_id"]
    content, meta = await client.read(ptr["artifact_id"])
    assert content == b"hello"
    assert meta["content_length"] == 5


async def test_memory_read_missing() -> None:
    import pytest

    client = InMemoryCatalogueClient()
    with pytest.raises(KeyError):
        await client.read("nonexistent")


# --- FilesystemStorage ---


async def test_filesystem_write_read(tmp_path: Path) -> None:
    from monet.catalogue._metadata import ArtifactMetadata

    storage = FilesystemStorage(tmp_path)
    metadata = ArtifactMetadata(
        artifact_id="art-1",
        content_type="text/plain",
        content_length=4,
        summary="test",
        confidence=0.9,
        completeness="complete",
        sensitivity_label="internal",
        agent_id=None,
        run_id=None,
        trace_id=None,
        tags={},
        created_at="2024-01-01T00:00:00Z",
    )
    ptr = await storage.write(b"data", metadata)
    assert "art-1" in ptr["url"]
    content, read_meta = await storage.read("art-1")
    assert content == b"data"
    assert read_meta["content_type"] == "text/plain"


async def test_filesystem_meta_json_exists(tmp_path: Path) -> None:
    from monet.catalogue._metadata import ArtifactMetadata

    storage = FilesystemStorage(tmp_path)
    metadata = ArtifactMetadata(
        artifact_id="art-2",
        content_type="text/plain",
        content_length=1,
        summary="x",
        confidence=0.0,
        completeness="complete",
        sensitivity_label="internal",
        agent_id=None,
        run_id=None,
        trace_id=None,
        tags={},
        created_at="2024-01-01T00:00:00Z",
    )
    await storage.write(b"x", metadata)
    assert (tmp_path / "art-2" / "meta.json").exists()
    assert (tmp_path / "art-2" / "content").exists()


async def test_filesystem_read_missing(tmp_path: Path) -> None:
    import pytest

    storage = FilesystemStorage(tmp_path)
    with pytest.raises(KeyError):
        await storage.read("missing")


# --- SQLiteIndex ---


async def test_index_insert_and_query() -> None:
    from monet.catalogue._metadata import ArtifactMetadata

    index = SQLiteIndex("sqlite+aiosqlite:///:memory:")
    await index.initialise()
    metadata = ArtifactMetadata(
        artifact_id="art-1",
        content_type="text/plain",
        content_length=10,
        summary="test",
        confidence=0.9,
        completeness="complete",
        sensitivity_label="internal",
        agent_id="test-agent",
        run_id="r-1",
        trace_id="t-1",
        tags={},
        created_at="2024-01-01T00:00:00Z",
    )
    await index.put(metadata)
    row = await index.get("art-1")
    assert row is not None
    assert row["artifact_id"] == "art-1"
    assert row["trace_id"] == "t-1"


async def test_index_query_missing() -> None:
    index = SQLiteIndex("sqlite+aiosqlite:///:memory:")
    await index.initialise()
    assert await index.get("missing") is None


async def test_index_query_by_run() -> None:
    from monet.catalogue._metadata import ArtifactMetadata

    index = SQLiteIndex("sqlite+aiosqlite:///:memory:")
    await index.initialise()
    metadata = ArtifactMetadata(
        artifact_id="art-run",
        content_type="text/plain",
        content_length=5,
        summary="test",
        confidence=0.5,
        completeness="complete",
        sensitivity_label="internal",
        agent_id=None,
        run_id="run-42",
        trace_id=None,
        tags={},
        created_at="2024-01-01T00:00:00Z",
    )
    await index.put(metadata)
    results = await index.query_by_run("run-42")
    assert len(results) == 1
    assert results[0]["artifact_id"] == "art-run"


# --- CatalogueService (integration of storage + index) ---


async def test_service_write_read(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    index = SQLiteIndex("sqlite+aiosqlite:///:memory:")
    await index.initialise()
    service = CatalogueService(storage, index)

    ptr = await service.write(
        b"catalogue content",
        content_type="text/plain",
        summary="test",
        confidence=0.9,
        completeness="complete",
    )
    assert ptr["artifact_id"]
    assert ptr["url"]

    content, meta = await service.read(ptr["artifact_id"])
    assert content == b"catalogue content"
    assert meta["content_length"] == len(b"catalogue content")
