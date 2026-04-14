"""Artifact store — storage, metadata index, service, and configuration."""

from __future__ import annotations

from pathlib import Path

from monet.config import ArtifactsConfig
from monet.core.artifacts import _set_artifact_backend

from ._index import SQLiteIndex
from ._memory import InMemoryArtifactClient
from ._metadata import ArtifactMetadata
from ._protocol import ArtifactClient
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

    Args:
        default_root: Fallback path when ``MONET_ARTIFACTS_DIR`` is unset.
            Defaults to ``Path(".artifacts")``.

    Returns:
        A configured :class:`ArtifactService`.
    """
    fallback = default_root or Path(".artifacts")
    root = ArtifactsConfig.load().resolve_root(fallback)
    root.mkdir(parents=True, exist_ok=True)
    return ArtifactService(
        storage=FilesystemStorage(root=root / "blobs"),
        index=SQLiteIndex(db_url=f"sqlite+aiosqlite:///{root / 'index.db'}"),
    )


__all__ = [
    "ArtifactClient",
    "ArtifactMetadata",
    "ArtifactService",
    "FilesystemStorage",
    "InMemoryArtifactClient",
    "SQLiteIndex",
    "artifacts_from_env",
    "configure_artifacts",
]
