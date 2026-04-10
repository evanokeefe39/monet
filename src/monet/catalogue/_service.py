"""Reference implementation of CatalogueClient.

Wires FilesystemStorage (bytes on disk) with SQLiteIndex (queryable metadata).
Production applications implement CatalogueClient directly against their
own storage backend. This service is for development and simple deployments.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from monet.catalogue._index import SQLiteIndex
from monet.catalogue._metadata import ArtifactMetadata
from monet.catalogue._storage import FilesystemStorage
from monet.types import ArtifactPointer


class CatalogueService:
    """Composes FilesystemStorage and SQLiteIndex."""

    def __init__(self, storage: FilesystemStorage, index: SQLiteIndex) -> None:
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
        self,
        content: bytes,
        content_type: str,
        summary: str,
        confidence: float,
        completeness: str,
        sensitivity_label: str = "internal",
        **kwargs: object,
    ) -> ArtifactPointer:
        """Write an artifact to storage and index.

        Auto-pulls run context for agent_id/run_id/trace_id if available.
        Context is optional — write() can be called outside the decorator.
        """
        await self._ensure_initialised()
        # Get run context if available — not required
        try:
            from monet.core.context import get_run_context

            ctx = get_run_context()
            run_id = ctx.get("run_id")
            trace_id = ctx.get("trace_id")
            agent_id = ctx.get("agent_id")
        except (LookupError, RuntimeError):
            run_id = trace_id = agent_id = None

        artifact_id = str(uuid.uuid4())
        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            content_type=content_type,
            content_length=len(content),
            summary=summary,
            confidence=confidence,
            completeness=completeness,
            sensitivity_label=sensitivity_label,
            agent_id=agent_id,
            run_id=run_id,
            trace_id=trace_id,
            tags=dict(kwargs.get("tags", {})) if "tags" in kwargs else {},
            created_at=datetime.now(tz=UTC).isoformat(),
        )
        pointer = await self._storage.write(content, metadata)
        await self._index.put(metadata)
        return pointer

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Read an artifact from storage."""
        await self._ensure_initialised()
        return await self._storage.read(artifact_id)
