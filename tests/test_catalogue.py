"""Tests for the artifact catalogue."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from monet.catalogue._index import SQLiteIndex
from monet.catalogue._memory import InMemoryCatalogueClient
from monet.catalogue._metadata import ArtifactMetadata
from monet.catalogue._service import CatalogueService
from monet.catalogue._storage import FilesystemStorage

if TYPE_CHECKING:
    from pathlib import Path


def _meta(**overrides: object) -> ArtifactMetadata:
    """Build valid metadata with sensible defaults."""
    defaults: dict[str, object] = {
        "content_type": "text/plain",
        "created_by": "test-agent/1.0",
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return ArtifactMetadata(**defaults)  # type: ignore[arg-type]


# --- Metadata validation ---


def test_valid_metadata() -> None:
    m = _meta()
    assert m.sensitivity_label == "internal"
    assert m.completeness == "complete"


def test_invalid_sensitivity_label() -> None:
    with pytest.raises(ValidationError):
        _meta(sensitivity_label="top_secret")


def test_invalid_completeness() -> None:
    with pytest.raises(ValidationError):
        _meta(completeness="maybe")


def test_pii_requires_retention() -> None:
    with pytest.raises(ValidationError):
        _meta(pii_flag=True)


def test_pii_with_retention_valid() -> None:
    m = _meta(pii_flag=True, retention_policy="90d")
    assert m.pii_flag is True
    assert m.retention_policy == "90d"


# --- InMemoryCatalogueClient ---


def test_memory_write_read() -> None:
    client = InMemoryCatalogueClient()
    ptr = client.write(b"hello", _meta())
    assert ptr.artifact_id
    content, meta = client.read(ptr.artifact_id)
    assert content == b"hello"
    assert meta.content_length == 5


def test_memory_read_missing() -> None:
    client = InMemoryCatalogueClient()
    with pytest.raises(KeyError):
        client.read("nonexistent")


# --- FilesystemStorage ---


def test_filesystem_write_read(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    meta_dict = {"test": "value"}
    url = storage.write("art-1", b"data", meta_dict)
    assert "art-1" in url
    content, read_dict = storage.read("art-1")
    assert content == b"data"
    assert read_dict["test"] == "value"


def test_filesystem_meta_json_exists(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    storage.write("art-2", b"x", {"key": "val"})
    assert (tmp_path / "art-2" / "meta.json").exists()
    assert (tmp_path / "art-2" / "content").exists()


def test_filesystem_read_missing(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    with pytest.raises(KeyError):
        storage.read("missing")


# --- SQLiteIndex ---


def test_index_insert_and_query() -> None:
    index = SQLiteIndex("sqlite:///:memory:")
    meta = _meta(
        artifact_id="art-1",
        trace_id="t-1",
        run_id="r-1",
        created_at="2024-01-01T00:00:00Z",
    )
    index.insert(meta)

    row = index.query_by_id("art-1")
    assert row is not None
    assert row["artifact_id"] == "art-1"
    assert row["trace_id"] == "t-1"


def test_index_query_missing() -> None:
    index = SQLiteIndex("sqlite:///:memory:")
    assert index.query_by_id("missing") is None


def test_index_query_by_trace() -> None:
    index = SQLiteIndex("sqlite:///:memory:")
    for i in range(3):
        meta = _meta(
            artifact_id=f"art-{i}",
            trace_id="shared-trace",
            created_at="2024-01-01T00:00:00Z",
        )
        index.insert(meta)

    results = index.query_by_trace("shared-trace")
    assert len(results) == 3


def test_index_query_by_run() -> None:
    index = SQLiteIndex("sqlite:///:memory:")
    meta = _meta(
        artifact_id="art-run",
        run_id="run-42",
        created_at="2024-01-01T00:00:00Z",
    )
    index.insert(meta)
    results = index.query_by_run("run-42")
    assert len(results) == 1
    assert results[0]["artifact_id"] == "art-run"


# --- CatalogueService (integration of storage + index) ---


def test_service_write_read(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    index = SQLiteIndex("sqlite:///:memory:")
    service = CatalogueService(storage, index)

    ptr = service.write(b"catalogue content", _meta())
    assert ptr.artifact_id
    assert ptr.url

    content, meta = service.read(ptr.artifact_id)
    assert content == b"catalogue content"
    assert meta.content_length == len(b"catalogue content")
    assert meta.content_hash  # Computed


def test_service_computes_hash(tmp_path: Path) -> None:
    import hashlib

    storage = FilesystemStorage(tmp_path)
    index = SQLiteIndex("sqlite:///:memory:")
    service = CatalogueService(storage, index)

    data = b"hash verification"
    ptr = service.write(data, _meta())
    _, meta = service.read(ptr.artifact_id)
    assert meta.content_hash == hashlib.sha256(data).hexdigest()


def test_service_indexes_metadata(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    index = SQLiteIndex("sqlite:///:memory:")
    service = CatalogueService(storage, index)

    service.write(b"x", _meta(trace_id="t-idx"))
    results = index.query_by_trace("t-idx")
    assert len(results) == 1
