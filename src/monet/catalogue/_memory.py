"""In-memory catalogue client for tests."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from monet._types import ArtifactPointer

if TYPE_CHECKING:
    from ._metadata import ArtifactMetadata


class InMemoryCatalogueClient:
    """Dict-backed catalogue for tests. No I/O, no database."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, ArtifactMetadata]] = {}

    def write(self, content: bytes, metadata: ArtifactMetadata) -> ArtifactPointer:
        """Write content and metadata to in-memory store."""
        if not metadata.artifact_id:
            metadata.artifact_id = str(uuid.uuid4())

        metadata.content_length = len(content)
        metadata.content_hash = hashlib.sha256(content).hexdigest()
        if not metadata.created_at:
            metadata.created_at = datetime.now(tz=UTC).isoformat()

        self._store[metadata.artifact_id] = (content, metadata)
        return ArtifactPointer(
            artifact_id=metadata.artifact_id,
            url=f"memory://{metadata.artifact_id}",
        )

    def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Read content and metadata from in-memory store."""
        if artifact_id not in self._store:
            msg = f"Artifact not found: {artifact_id}"
            raise KeyError(msg)
        return self._store[artifact_id]
