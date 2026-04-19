"""Tests for the artifact artifact store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from monet.artifacts._index import SQLiteIndex
from monet.artifacts._memory import InMemoryArtifactClient
from monet.artifacts._service import ArtifactService
from monet.artifacts._storage import FilesystemStorage

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
    assert meta["content_length"] == 5


async def test_memory_read_missing() -> None:
    import pytest

    client = InMemoryArtifactClient()
    with pytest.raises(KeyError):
        await client.read("nonexistent")


# --- FilesystemStorage ---


async def test_filesystem_write_read(tmp_path: Path) -> None:
    from monet.artifacts._metadata import ArtifactMetadata

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
    from monet.artifacts._metadata import ArtifactMetadata

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
    from monet.artifacts._metadata import ArtifactMetadata

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
    from monet.artifacts._metadata import ArtifactMetadata

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


# --- ArtifactService (integration of storage + index) ---


async def test_service_write_read(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    index = SQLiteIndex("sqlite+aiosqlite:///:memory:")
    await index.initialise()
    service = ArtifactService(storage, index)

    ptr = await service.write(
        b"artifact store content",
        content_type="text/plain",
        summary="test",
        confidence=0.9,
        completeness="complete",
    )
    assert ptr["artifact_id"]
    assert ptr["url"]

    content, meta = await service.read(ptr["artifact_id"])
    assert content == b"artifact store content"
    assert meta["content_length"] == len(b"artifact store content")


# --- Regression: no blocking syscalls on the artifact store hot path ---


def test_filesystem_storage_no_blocking_syscalls_in_write() -> None:
    """Regression guard for the BlockingError incident (c735f8f → 9638ecd).

    ``FilesystemStorage.write`` runs on the ASGI event loop under
    ``langgraph dev``. ``blockbuster`` intercepts sync filesystem
    syscalls there and raises ``BlockingError``. A previous "fix" for
    Windows file URIs called ``Path.resolve()`` in the write path,
    which invokes ``os.path.realpath`` → ``os.getcwd`` — both blocking.
    Every reference-agent invocation silently collapsed to an empty
    AgentResult until the root cause was found a session later.

    tasks/lessons.md names the absence of this test as the schema gap
    that made the detection slow. Close it via AST inspection: parse
    the storage module and assert no ``Path.resolve()`` /
    ``os.getcwd`` / ``os.path.realpath`` call appears anywhere in the
    module's top-level functions or methods. Cheap, deterministic,
    catches the regression shape regardless of how it's spelled.
    """
    import ast
    from pathlib import Path as _Path

    src = _Path("src/monet/artifacts/_storage.py").read_text(encoding="utf-8")
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
            # Path(...).resolve() calls — the attr is "resolve".
            if chain and chain[-1] == "resolve":
                receiver = ".".join(chain[:-1]) or "<expr>"
                offenders.append(f"line {node.lineno}: .resolve() on {receiver}")
            # os.getcwd()
            if chain == ["os", "getcwd"]:
                offenders.append(f"line {node.lineno}: os.getcwd()")
            # os.path.realpath(...)
            if chain == ["os", "path", "realpath"]:
                offenders.append(f"line {node.lineno}: os.path.realpath()")

    assert not offenders, (
        "FilesystemStorage must not call blocking path syscalls on the "
        "ASGI event loop. Offenders:\n  " + "\n  ".join(offenders)
    )


# --- Corrupt metadata guard ---


async def test_filesystem_read_corrupt_meta_json(tmp_path: Path) -> None:
    """FilesystemStorage.read raises ValueError on malformed meta.json."""
    import pytest

    art_dir = tmp_path / "corrupt-art"
    art_dir.mkdir()
    (art_dir / "content").write_bytes(b"data")
    (art_dir / "meta.json").write_text("not valid json {{{")

    storage = FilesystemStorage(tmp_path)
    with pytest.raises(ValueError, match="Corrupt metadata for artifact corrupt-art"):
        await storage.read("corrupt-art")


# --- Artifact store service exception narrowing ---


async def test_service_write_propagates_unexpected_exceptions(tmp_path: Path) -> None:
    """ArtifactService.write does not swallow unexpected exceptions from
    get_run_context — only LookupError and RuntimeError are caught."""
    from unittest.mock import patch

    storage = FilesystemStorage(tmp_path)
    index = SQLiteIndex("sqlite+aiosqlite:///:memory:")
    await index.initialise()
    service = ArtifactService(storage, index)

    with patch(
        "monet.core.context.get_run_context",
        side_effect=TypeError("unexpected"),
    ):
        import pytest

        with pytest.raises(TypeError, match="unexpected"):
            await service.write(
                b"test",
                content_type="text/plain",
                summary="test",
                confidence=0.9,
                completeness="complete",
            )


# --- Regression: relative roots must not leak into file:// URIs ---


def test_filesystem_storage_relative_root_becomes_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FilesystemStorage normalises a relative root to absolute at construction.

    Regression: ``artifacts_from_env()`` constructs the storage with
    ``Path(".artifacts")`` by default. ``Path.as_uri()`` refuses to format
    a relative path as ``file://``, so every ``write()`` call used to raise
    ``ValueError: relative path can't be expressed as a file URI`` after
    content was already on disk. Enforcing absoluteness in ``__init__``
    closes the contract gap.
    """
    monkeypatch.chdir(tmp_path)
    storage = FilesystemStorage("relative/root")
    assert storage.root.is_absolute()
    # And the resolved root lives under the new cwd, as expected.
    assert storage.root == (tmp_path / "relative" / "root").absolute()


async def test_artifacts_from_env_default_root_produces_absolute_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end regression: the default (relative) artifact store root must
    round-trip through write() and produce a valid file:// URI. Before the
    fix this raised ``ValueError: relative path can't be expressed as a
    file URI`` inside every agent invocation that wrote an artifact.
    """
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
    # The URL must round-trip back to an existing file on disk.
    parsed = urlparse(ptr["url"])
    # On Windows the path component looks like "/C:/Users/..."; strip the
    # leading slash and URL-decode before handing to pathlib.
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    from pathlib import Path as _Path

    assert _Path(raw_path).exists(), f"round-tripped path missing: {raw_path}"
