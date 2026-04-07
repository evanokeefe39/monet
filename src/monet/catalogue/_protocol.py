"""CatalogueClient protocol — the interface any catalogue implementation must satisfy.

The monet SDK ships a reference implementation (CatalogueService + FilesystemStorage
+ SQLiteIndex) suitable for development and simple deployments.

Production applications implement this protocol against their own backend:
S3, GCS, a content management system, an existing artifact store, etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from monet.types import ArtifactPointer

if TYPE_CHECKING:
    from monet.catalogue._metadata import ArtifactMetadata


@runtime_checkable
class CatalogueClient(Protocol):
    """Abstract interface for catalogue operations."""

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
        """Write content to the catalogue."""
        ...

    async def read(
        self,
        artifact_id: str,
    ) -> tuple[bytes, ArtifactMetadata]:
        """Read content and metadata from the catalogue."""
        ...
