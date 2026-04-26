"""Tests for the artifact store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from monet.artifacts._memory import InMemoryArtifactClient
from monet.artifacts.prebuilt._index import SQLiteIndex
from monet.artifacts.prebuilt._service import ArtifactService
from monet.artifacts.prebuilt._storage import FsspecStorage

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


# --- InMemoryArtifactClient ---


async def test_memory_write_read() -> None:
    client = InMemoryArtifactClient()
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
    assert meta.get("content_type") == "text/plain"


async def test_memory_read_missing() -> None:
    import pytest

    client = InMemoryArtifactClient()
    with pytest.raises(KeyError):
        await client.read("nonexistent")


async def test_memory_list() -> None:
    client = InMemoryArtifactClient()
    await client.write(b"a", content_type="text/plain")
    await client.write(b"b", content_type="text/plain")
    pointers = await client.list()
    assert len(pointers) == 2
    for p in pointers:
        assert p["url"].startswith("memory://")


async def test_memory_write_key() -> None:
    client = InMemoryArtifactClient()
    ptr = await client.write(b"data", content_type="text/plain", key="my_key")
    assert ptr.get("key") == "my_key"


# --- FsspecStorage ---


async def test_fsspec_write_read(tmp_path: Path) -> None:
    storage = FsspecStorage(tmp_path.as_uri())
    ptr = await storage.write(b"data", "art-1")
    assert ptr["artifact_id"] == "art-1"
    assert "art-1" in ptr["url"]
    content = await storage.read("art-1")
    assert content == b"data"


async def test_fsspec_read_missing(tmp_path: Path) -> None:
    import pytest

    storage = FsspecStorage(tmp_path.as_uri())
    with pytest.raises(KeyError):
        await storage.read("missing")


# --- SQLiteIndex ---


async def test_index_insert_and_query() -> None:
    from monet.artifacts.prebuilt._metadata import ArtifactMetadata

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
        thread_id=None,
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
    from monet.artifacts.prebuilt._metadata import ArtifactMetadata

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
        thread_id=None,
        tags={},
        created_at="2024-01-01T00:00:00Z",
    )
    await index.put(metadata)
    results = await index.query_by_run("run-42")
    assert len(results) == 1
    assert results[0]["artifact_id"] == "art-run"


# --- ArtifactService (integration of storage + index) ---


async def test_service_write_read(tmp_path: Path) -> None:
    service = ArtifactService(
        storage_url=(tmp_path / "blobs").absolute().as_uri(),
        index_url="sqlite+aiosqlite:///:memory:",
    )
    body = b"artifact store content"
    ptr = await service.write(
        body,
        content_type="text/plain",
        summary="test",
        confidence=0.9,
        completeness="complete",
    )
    assert ptr["artifact_id"]
    assert ptr["url"]

    content, meta = await service.read(ptr["artifact_id"])
    assert content == body
    assert meta.get("content_length") == len(body)


async def test_service_query(tmp_path: Path) -> None:
    service = ArtifactService(
        storage_url=(tmp_path / "blobs").absolute().as_uri(),
        index_url="sqlite+aiosqlite:///:memory:",
    )
    await service.write(b"x", content_type="text/plain", summary="first")
    await service.write(b"y", content_type="text/plain", summary="second")
    rows = await service.query(limit=10)
    assert len(rows) == 2


async def test_service_list(tmp_path: Path) -> None:
    service = ArtifactService(
        storage_url=(tmp_path / "blobs").absolute().as_uri(),
        index_url="sqlite+aiosqlite:///:memory:",
    )
    await service.write(b"x", content_type="text/plain")
    pointers = await service.list()
    assert len(pointers) == 1
    assert pointers[0]["artifact_id"]


# --- Regression: no blocking syscalls on the artifact store hot path ---


def test_fsspec_storage_no_blocking_syscalls_in_write() -> None:
    """FsspecStorage.write must not call blocking path syscalls on the
    ASGI event loop. Mirrors the guard that existed for FilesystemStorage."""
    import ast
    from pathlib import Path as _Path

    src = _Path("src/monet/artifacts/prebuilt/_storage.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    offenders: list[str] = []

    def _attr_chain(node: ast.AST) -> list[str]:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return list(reversed(parts))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            chain = _attr_chain(func)
            if chain and chain[-1] == "resolve":
                receiver = ".".join(chain[:-1]) or "<expr>"
                offenders.append(f"line {node.lineno}: .resolve() on {receiver}")
            if chain == ["os", "getcwd"]:
                offenders.append(f"line {node.lineno}: os.getcwd()")
            if chain == ["os", "path", "realpath"]:
                offenders.append(f"line {node.lineno}: os.path.realpath()")

    assert not offenders, (
        "FsspecStorage must not call blocking path syscalls on the "
        "ASGI event loop. Offenders:\n  " + "\n  ".join(offenders)
    )


# --- ArtifactStore (SDK handle) exception narrowing ---


async def test_store_write_propagates_unexpected_exceptions(tmp_path: Path) -> None:
    """ArtifactStore.write does not swallow unexpected exceptions from
    get_run_context — only LookupError and RuntimeError are caught."""
    from unittest.mock import patch

    import pytest

    from monet.artifacts import configure_artifacts
    from monet.core.artifacts import get_artifacts

    service = ArtifactService(
        storage_url=(tmp_path / "blobs").absolute().as_uri(),
        index_url="sqlite+aiosqlite:///:memory:",
    )
    configure_artifacts(service)
    try:
        with (
            patch(
                "monet.core.context.get_run_context",
                side_effect=TypeError("unexpected"),
            ),
            pytest.raises(TypeError, match="unexpected"),
        ):
            await get_artifacts().write(
                b"test",
                content_type="text/plain",
                summary="test",
                confidence=0.9,
                completeness="complete",
            )
    finally:
        configure_artifacts(None)


# --- artifacts_from_env produces absolute file:// URI ---


async def test_artifacts_from_env_default_root_produces_absolute_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end regression: artifacts_from_env() must produce file:// URIs
    and artifacts must be readable after writing."""
    from urllib.parse import unquote, urlparse

    from monet.artifacts import artifacts_from_env

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MONET_ARTIFACTS_DIR", raising=False)

    service = artifacts_from_env()
    ptr = await service.write(
        b"hello",
        content_type="text/plain",
        summary="regression",
        confidence=1.0,
        completeness="complete",
    )

    assert ptr["url"].startswith("file://")
    parsed = urlparse(ptr["url"])
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    from pathlib import Path as _Path

    assert _Path(raw_path).exists(), f"round-tripped path missing: {raw_path}"
