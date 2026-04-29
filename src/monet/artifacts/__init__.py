"""Artifact store — protocol, in-memory stub, reference implementation."""

from __future__ import annotations

from monet.artifacts.prebuilt import ArtifactService, artifacts_from_env
from monet.core.artifacts import (
    ArtifactStore,
    _set_artifact_backend,
)

from ._memory import InMemoryArtifactClient
from ._protocol import (
    ArtifactClient,
    ArtifactQueryable,
    ArtifactReader,
    ArtifactWriter,
)


def configure_artifacts(client: ArtifactClient | None) -> None:
    """Configure the artifact store backend for this application.

    Call once at startup before any agents are invoked. Parallels
    configure_tracing(). Application supplies the implementation,
    SDK defines the interface.

    Pass None to reset — useful in test fixtures.

    Example:
        from monet.artifacts import configure_artifacts, ArtifactService
        configure_artifacts(ArtifactService(
            storage_url="file:///path/to/blobs",
            index_url="sqlite+aiosqlite:///path/to/index.db",
        ))
    """
    _set_artifact_backend(client)


__all__ = [
    "ArtifactClient",
    "ArtifactQueryable",
    "ArtifactReader",
    "ArtifactService",
    "ArtifactStore",
    "ArtifactWriter",
    "InMemoryArtifactClient",
    "artifacts_from_env",
    "configure_artifacts",
]
