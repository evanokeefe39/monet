"""Abstract catalogue client protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from monet._types import ArtifactPointer
    from monet.catalogue._metadata import ArtifactMetadata


@runtime_checkable
class CatalogueClient(Protocol):
    """Abstract interface for catalogue operations.

    Implementations: InMemoryCatalogueClient (tests),
    CatalogueService (local), HttpCatalogueClient (distributed).
    """

    def write(self, content: bytes, metadata: ArtifactMetadata) -> ArtifactPointer:
        """Write content and metadata to the catalogue.

        Preconditions:
            All mandatory metadata fields present.
            Write-time invariants pass validation.
        Postconditions:
            Content stored, metadata indexed, hash computed.
            Returns ArtifactPointer with artifact_id and url.
        """
        ...

    def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Read content and metadata from the catalogue.

        Preconditions:
            artifact_id exists in the catalogue.
        Postconditions:
            Content integrity verified via hash.
        """
        ...
