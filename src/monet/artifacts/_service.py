"""Reference implementation of ArtifactClient.

Wires StorageBackend (bytes on disk) with MetadataIndex (queryable metadata).
Production applications implement ArtifactClient directly against their
own storage backend. This service is for development and simple deployments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.artifacts._metadata import ArtifactMetadata
    from monet.artifacts._protocol import MetadataIndex, StorageBackend
    from monet.types import ArtifactPointer


class ArtifactService:
    """Composes StorageBackend and MetadataIndex."""

    def __init__(self, storage: StorageBackend, index: MetadataIndex) -> None:
        self._storage = storage
        self._index = index
        self._initialised = False

    async def initialise(self) -> None:
        """Ensure index tables exist. Idempotent. Called automatically on
        first read/write — explicit invocation at startup is optional but
        avoids the first-call latency hit.
        """
        if self._initialised:
            return
        await self._index.initialise()
        self._initialised = True

    async def _ensure_initialised(self) -> None:
        if not self._initialised:
            await self.initialise()

    async def write(
        self, content: bytes, metadata: ArtifactMetadata
    ) -> ArtifactPointer:
        """Write an artifact to storage and index."""
        await self._ensure_initialised()
        pointer = await self._storage.write(content, metadata)
        await self._index.put(metadata)
        return pointer

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Read an artifact from storage."""
        await self._ensure_initialised()
        return await self._storage.read(artifact_id)

    async def query_recent(
        self,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[ArtifactMetadata]:
        """Pass-through to index query_recent."""
        await self._ensure_initialised()
        return await self._index.query_recent(
            agent_id=agent_id,
            thread_id=thread_id,
            tag=tag,
            since=since,
            limit=limit,
        )

    async def count_per_thread(self, thread_ids: list[str]) -> dict[str, int]:
        """Return artifact count keyed by thread_id."""
        await self._ensure_initialised()
        return await self._index.count_per_thread(thread_ids)
