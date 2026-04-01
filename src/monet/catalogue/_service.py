"""CatalogueService — composes storage backend + metadata index."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from monet._types import ArtifactPointer

from ._metadata import ArtifactMetadata

if TYPE_CHECKING:
    from ._index import SQLiteIndex
    from ._storage import StorageBackend


class CatalogueService:
    """Composes a storage backend and metadata index.

    Enforces write-time invariants, computes content hash,
    and manages artifact lifecycle.
    """

    def __init__(self, storage: StorageBackend, index: SQLiteIndex) -> None:
        self._storage = storage
        self._index = index

    def write(self, content: bytes, metadata: ArtifactMetadata) -> ArtifactPointer:
        """Write an artifact to storage and index.

        Preconditions:
            metadata passes pydantic validation (invariants enforced).
        Postconditions:
            Content stored, metadata indexed, hash computed.
        """
        # Generate ID if not provided
        if not metadata.artifact_id:
            metadata.artifact_id = str(uuid.uuid4())

        # Compute derived fields
        metadata.content_length = len(content)
        metadata.content_hash = hashlib.sha256(content).hexdigest()
        if not metadata.created_at:
            metadata.created_at = datetime.now(tz=UTC).isoformat()

        # Write to storage
        meta_dict = metadata.model_dump()
        url = self._storage.write(metadata.artifact_id, content, meta_dict)

        # Index metadata
        self._index.insert(metadata)

        return ArtifactPointer(artifact_id=metadata.artifact_id, url=url)

    def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Read an artifact from storage and verify integrity.

        Preconditions:
            artifact_id exists in storage.
        Postconditions:
            Content hash verified against stored hash.
        """
        content, meta_dict = self._storage.read(artifact_id)
        metadata = ArtifactMetadata(**meta_dict)

        # Verify integrity
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != metadata.content_hash:
            msg = (
                f"Content hash mismatch for {artifact_id}: "
                f"expected {metadata.content_hash}, got {actual_hash}"
            )
            raise ValueError(msg)

        return content, metadata
