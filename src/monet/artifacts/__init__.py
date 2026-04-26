"""Artifact store — storage, metadata index, service, and configuration."""

from __future__ import annotations

from pathlib import Path

from monet.config import ArtifactsConfig
from monet.core.artifacts import (
    ArtifactStore,
    ArtifactStoreHandle,
    _set_artifact_backend,
)

from ._index import SQLiteIndex
from ._memory import InMemoryArtifactClient
from ._metadata import ArtifactMetadata
from ._protocol import (
    ArtifactClient,
    ArtifactQueryable,
    ArtifactReader,
    ArtifactWriter,
    MetadataIndex,
    StorageBackend,
)
from ._service import ArtifactService
from ._storage import FilesystemStorage


def configure_artifacts(client: ArtifactClient | None) -> None:
    """Configure the artifact store backend for this application.

    Call once at startup before any agents are invoked. Parallels
    configure_tracing(). Application supplies the implementation,
    SDK defines the interface.

    Pass None to reset — useful in test fixtures.

    Example:
        from monet.artifacts import (
            configure_artifacts, ArtifactService, FilesystemStorage, SQLiteIndex,
        )
        configure_artifacts(ArtifactService(
            storage=FilesystemStorage(".artifacts/blobs"),
            index=SQLiteIndex("sqlite+aiosqlite:///.artifacts/index.db"),
        ))
    """
    _set_artifact_backend(client)


def artifacts_from_env(*, default_root: Path | None = None) -> ArtifactService:
    """Create an :class:`ArtifactService` from env or fallback path.

    Resolves the root directory via :class:`ArtifactsConfig.load`
    (``MONET_ARTIFACTS_DIR`` env), falling back to ``default_root`` and
    finally to ``Path(".artifacts")``. Creates the directory if needed.

    Applies pending alembic migrations eagerly — this is the developer-
    ergonomic factory where "just works" is the contract. Production
    deploys that want explicit control should construct :class:`SQLiteIndex`
    directly and gate on ``monet db check`` in the deploy pipeline; the
    direct constructor path fails fast if the DB is not at head.

    Args:
        default_root: Fallback path when ``MONET_ARTIFACTS_DIR`` is unset.
            Defaults to ``Path(".artifacts")``.

    Returns:
        A configured :class:`ArtifactService`.
    """
    from monet.artifacts._migrations import apply_migrations

    fallback = default_root or Path(".artifacts")
    root = ArtifactsConfig.load().resolve_root(fallback)
    root.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite+aiosqlite:///{root / 'index.db'}"
    apply_migrations(db_url)
    return ArtifactService(
        storage=FilesystemStorage(root=root / "blobs"),
        index=SQLiteIndex(db_url=db_url),
    )


__all__ = [
    "ArtifactClient",
    "ArtifactMetadata",
    "ArtifactQueryable",
    "ArtifactReader",
    "ArtifactService",
    "ArtifactStore",
    "ArtifactStoreHandle",
    "ArtifactWriter",
    "FilesystemStorage",
    "InMemoryArtifactClient",
    "MetadataIndex",
    "SQLiteIndex",
    "StorageBackend",
    "artifacts_from_env",
    "configure_artifacts",
]
