"""Artifact backend protocols.

The monet SDK ships a reference implementation (ArtifactService + FilesystemStorage
+ SQLiteIndex) suitable for development and simple deployments.

Production applications implement these protocols against their own backend:
S3, GCS, a content management system, an existing artifact store, etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from monet.artifacts._metadata import ArtifactMetadata
    from monet.types import ArtifactPointer


@runtime_checkable
class ArtifactWriter(Protocol):
    """Write-only artifact backend contract."""

    async def write(
        self, content: bytes, metadata: ArtifactMetadata
    ) -> ArtifactPointer:
        """Persist content and metadata; return a pointer."""
        ...


@runtime_checkable
class ArtifactReader(Protocol):
    """Read-only artifact backend contract."""

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Retrieve content and metadata by artifact_id."""
        ...


@runtime_checkable
class ArtifactQueryable(Protocol):
    """Optional query capabilities for artifact backends that support them."""

    async def query_recent(
        self,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[ArtifactMetadata]:
        """List recent artifact metadata, filtered optionally.

        Ordered by ``created_at`` descending. ``since`` is an ISO-8601
        string. ``tag`` matches a tag key present in the artifact's
        tag dict. ``thread_id`` filters to artifacts written by agents
        running under a given chat / orchestrator thread.
        """
        ...

    async def count_per_thread(self, thread_ids: list[str]) -> dict[str, int]:
        """Return artifact count keyed by thread_id."""
        ...


@runtime_checkable
class ArtifactClient(ArtifactReader, ArtifactWriter, Protocol):
    """Minimum backend contract: read + write."""

    ...


class StorageBackend(Protocol):
    """Blob storage protocol."""

    async def write(
        self, content: bytes, metadata: ArtifactMetadata
    ) -> ArtifactPointer:
        """Write blob and return pointer."""
        ...

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Read blob and its metadata."""
        ...


class MetadataIndex(Protocol):
    """Metadata index protocol."""

    async def initialise(self) -> None:
        """Ensure index tables exist. Idempotent."""
        ...

    async def put(self, metadata: ArtifactMetadata) -> None:
        """Insert or replace metadata entry."""
        ...

    async def get(self, artifact_id: str) -> ArtifactMetadata | None:
        """Fetch metadata by artifact_id."""
        ...

    async def query_by_run(self, run_id: str) -> list[ArtifactMetadata]:
        """Return all artifacts for a run."""
        ...

    async def query_recent(
        self,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[ArtifactMetadata]:
        """List recent artifact metadata, filtered optionally."""
        ...

    async def count_per_thread(self, thread_ids: list[str]) -> dict[str, int]:
        """Return artifact count keyed by thread_id."""
        ...
