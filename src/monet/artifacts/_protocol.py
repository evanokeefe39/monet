"""ArtifactClient protocol — the interface artifact store implementations satisfy.

The monet SDK ships a reference implementation (ArtifactService + FilesystemStorage
+ SQLiteIndex) suitable for development and simple deployments.

Production applications implement this protocol against their own backend:
S3, GCS, a content management system, an existing artifact store, etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from monet.artifacts._metadata import ArtifactMetadata
    from monet.types import ArtifactPointer


@runtime_checkable
class ArtifactClient(Protocol):
    """Abstract interface for artifact store operations."""

    async def write(
        self,
        content: bytes,
        content_type: str,
        summary: str,
        confidence: float,
        completeness: str,
        sensitivity_label: str = "internal",
        **kwargs: object,
    ) -> ArtifactPointer:
        """Write content to the artifact store."""
        ...

    async def read(
        self,
        artifact_id: str,
    ) -> tuple[bytes, ArtifactMetadata]:
        """Read content and metadata from the artifact store."""
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
        """List recent artifact metadata, filtered optionally.

        Ordered by ``created_at`` descending. ``since`` is an ISO-8601
        string. ``tag`` matches a tag key present in the artifact's
        tag dict. ``thread_id`` filters to artifacts written by agents
        running under a given chat / orchestrator thread. Backends that
        cannot efficiently query may raise ``NotImplementedError``.
        """
        ...
