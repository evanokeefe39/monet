"""Tests for Spike 3: Catalogue Interface and Stub.

Success criteria from SPIKES.md: an agent test using InMemoryCatalogueClient
produces the same AgentResult shape as a test using FilesystemCatalogueClient.
The agent function doesn't reference any specific implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .protocol import (
    ArtifactMetadata,
    CatalogueClient,
    FilesystemCatalogueClient,
    InMemoryCatalogueClient,
    validate_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path

# --- Fixture: both implementations as parametrized ---


@pytest.fixture(params=["memory", "filesystem"])
def client(request: pytest.FixtureRequest, tmp_path: Path) -> CatalogueClient:
    if request.param == "memory":
        return InMemoryCatalogueClient()
    return FilesystemCatalogueClient(tmp_path)


def _make_metadata(**overrides: object) -> ArtifactMetadata:
    """Build valid metadata with sensible defaults."""
    defaults: dict[str, object] = {
        "artifact_id": "",
        "content_type": "text/plain",
        "content_length": 0,
        "content_hash": "",
        "summary": "Test artifact",
        "created_by": "test-agent/1.0",
        "trace_id": "trace-001",
        "run_id": "run-001",
        "invocation_command": "fast",
        "confidence": 0.9,
        "completeness": "complete",
        "sensitivity_label": "internal",
        "data_residency": "local",
        "pii_flag": False,
    }
    defaults.update(overrides)
    return ArtifactMetadata(**defaults)  # type: ignore[arg-type]


# --- Write and read roundtrip (parametrized across both impls) ---


def test_write_read_roundtrip(client: CatalogueClient) -> None:
    content = b"Hello, catalogue!"
    meta = _make_metadata()

    pointer = client.write(content, meta)
    assert pointer.artifact_id
    assert pointer.url

    read_content, read_meta = client.read(pointer.artifact_id)
    assert read_content == content
    assert read_meta.content_type == "text/plain"
    assert read_meta.summary == "Test artifact"
    assert read_meta.content_length == len(content)
    assert read_meta.content_hash  # Should be computed


def test_write_computes_hash(client: CatalogueClient) -> None:
    content = b"hash me"
    meta = _make_metadata()
    pointer = client.write(content, meta)
    _, read_meta = client.read(pointer.artifact_id)

    import hashlib

    expected = hashlib.sha256(content).hexdigest()
    assert read_meta.content_hash == expected


def test_write_sets_content_length(client: CatalogueClient) -> None:
    content = b"twelve bytes"
    meta = _make_metadata()
    pointer = client.write(content, meta)
    _, read_meta = client.read(pointer.artifact_id)
    assert read_meta.content_length == len(content)


def test_read_missing_raises(client: CatalogueClient) -> None:
    with pytest.raises(KeyError):
        client.read("nonexistent-id")


# --- Pointer shape is identical regardless of implementation ---


def test_pointer_shape_identical(tmp_path: Path) -> None:
    """Both implementations produce ArtifactPointer with same fields."""
    content = b"test"
    meta_mem = _make_metadata()
    meta_fs = _make_metadata()

    mem = InMemoryCatalogueClient()
    fs = FilesystemCatalogueClient(tmp_path)

    ptr_mem = mem.write(content, meta_mem)
    ptr_fs = fs.write(content, meta_fs)

    # Both have artifact_id and url
    assert ptr_mem.artifact_id
    assert ptr_mem.url
    assert ptr_fs.artifact_id
    assert ptr_fs.url
    # URLs differ in scheme but both are present
    assert "://" in ptr_mem.url
    assert "://" in ptr_fs.url


# --- Write-time invariant validation ---


def test_invalid_sensitivity_label() -> None:
    meta = _make_metadata(sensitivity_label="top_secret")
    with pytest.raises(ValueError, match="sensitivity_label"):
        validate_metadata(meta)


def test_invalid_completeness() -> None:
    meta = _make_metadata(completeness="maybe")
    with pytest.raises(ValueError, match="completeness"):
        validate_metadata(meta)


def test_pii_requires_retention() -> None:
    meta = _make_metadata(pii_flag=True, retention_policy=None)
    with pytest.raises(ValueError, match="retention_policy"):
        validate_metadata(meta)


def test_pii_with_retention_valid(client: CatalogueClient) -> None:
    meta = _make_metadata(pii_flag=True, retention_policy="90d")
    pointer = client.write(b"pii data", meta)
    _, read_meta = client.read(pointer.artifact_id)
    assert read_meta.pii_flag is True
    assert read_meta.retention_policy == "90d"


# --- Protocol conformance ---


def test_in_memory_is_catalogue_client() -> None:
    assert isinstance(InMemoryCatalogueClient(), CatalogueClient)


def test_filesystem_is_catalogue_client(tmp_path: Path) -> None:
    assert isinstance(FilesystemCatalogueClient(tmp_path), CatalogueClient)


# --- Filesystem-specific: meta.json sidecar ---


def test_filesystem_writes_meta_json(tmp_path: Path) -> None:
    client = FilesystemCatalogueClient(tmp_path)
    meta = _make_metadata()
    pointer = client.write(b"sidecar test", meta)

    meta_path = tmp_path / pointer.artifact_id / "meta.json"
    assert meta_path.exists()

    import json

    sidecar = json.loads(meta_path.read_text())
    assert sidecar["content_type"] == "text/plain"
    assert sidecar["summary"] == "Test artifact"
