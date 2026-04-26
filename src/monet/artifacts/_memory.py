"""In-memory artifact client for unit tests only. Not for production."""

from __future__ import annotations

import uuid
from typing import Any

from monet.types import ArtifactPointer


class InMemoryArtifactClient:
    """Dict-backed artifact store for tests. No I/O, no database."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, dict[str, Any]]] = {}

    async def write(self, content: bytes, **kwargs: Any) -> ArtifactPointer:
        """Write content to in-memory store. artifact_id kwarg overrides uuid."""
        artifact_id = str(kwargs.get("artifact_id") or uuid.uuid4())
        url = f"memory://{artifact_id}"
        self._store[artifact_id] = (content, dict(kwargs))
        pointer = ArtifactPointer(artifact_id=artifact_id, url=url)
        key = kwargs.get("key")
        if key is not None:
            pointer["key"] = str(key)
        return pointer

    async def read(self, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
        """Read content and metadata from in-memory store."""
        if artifact_id not in self._store:
            msg = f"Artifact not found: {artifact_id}"
            raise KeyError(msg)
        return self._store[artifact_id]

    async def list(
        self, *, limit: int = 100, cursor: str | None = None
    ) -> list[ArtifactPointer]:
        """Return pointers for stored artifacts in insertion order."""
        ids = list(self._store.keys())[:limit]
        return [ArtifactPointer(artifact_id=aid, url=f"memory://{aid}") for aid in ids]
