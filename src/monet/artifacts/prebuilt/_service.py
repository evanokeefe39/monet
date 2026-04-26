"""Reference ArtifactClient implementation. Internal to prebuilt."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import builtins

from monet.artifacts.prebuilt._index import SQLiteIndex
from monet.artifacts.prebuilt._metadata import ArtifactMetadata
from monet.artifacts.prebuilt._storage import FsspecStorage
from monet.types import ArtifactPointer


class ArtifactService:
    """Composes FsspecStorage (bytes) and SQLiteIndex (metadata).

    Implements the ArtifactClient protocol plus concrete query/count methods
    not on the protocol — callers that need them check isinstance.
    """

    def __init__(self, storage_url: str, index_url: str) -> None:
        self._storage = FsspecStorage(storage_url)
        self._index = SQLiteIndex(index_url)
        self._initialised = False

    async def initialise(self) -> None:
        """Ensure index tables exist. Idempotent."""
        if self._initialised:
            return
        await self._index.initialise()
        self._initialised = True

    async def _ensure_initialised(self) -> None:
        if not self._initialised:
            await self.initialise()

    # -- ArtifactClient protocol ------------------------------------------

    async def write(self, content: bytes, **kwargs: Any) -> ArtifactPointer:
        """Write artifact. Recognised kwargs:
        content_type, summary, confidence, completeness, sensitivity_label,
        tags, key, agent_run_ctxt (AgentRunContext injected by ArtifactStore).
        """
        await self._ensure_initialised()

        content_type = str(kwargs.get("content_type", "application/octet-stream"))
        summary = str(kwargs.get("summary", ""))
        confidence = float(kwargs.get("confidence", 0.0))
        completeness = str(kwargs.get("completeness", "complete"))
        sensitivity_label = str(kwargs.get("sensitivity_label", "internal"))
        tags = dict(kwargs.get("tags") or {})
        key: str | None = str(kwargs["key"]) if "key" in kwargs else None

        agent_run_ctxt: Any = kwargs.get("agent_run_ctxt")
        run_id: str | None = None
        trace_id: str | None = None
        agent_id: str | None = None
        thread_id: str | None = None
        if agent_run_ctxt is not None:
            run_id = agent_run_ctxt.get("run_id") or None
            trace_id = agent_run_ctxt.get("trace_id") or None
            agent_id = agent_run_ctxt.get("agent_id") or None
            thread_id = agent_run_ctxt.get("thread_id") or None

        artifact_id = f"{run_id}--{key}" if key and run_id else str(uuid.uuid4())

        pointer = await self._storage.write(content, artifact_id)

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
            thread_id=thread_id,
            tags=tags,
            created_at=datetime.now(tz=UTC).isoformat(),
        )
        await self._index.put(metadata)

        if key:
            pointer["key"] = key
        return pointer

    async def read(self, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
        """Read bytes and metadata dict for an artifact."""
        await self._ensure_initialised()
        content = await self._storage.read(artifact_id)
        idx_meta = await self._index.get(artifact_id)
        meta: dict[str, Any] = dict(idx_meta) if idx_meta is not None else {}
        return content, meta

    async def list(
        self, *, limit: int = 100, cursor: str | None = None
    ) -> list[ArtifactPointer]:
        """List artifact pointers, newest-first. cursor is ISO-8601 created_at."""
        await self._ensure_initialised()
        rows = await self._index.query_recent(since=cursor, limit=limit)
        return [
            ArtifactPointer(
                artifact_id=r["artifact_id"],
                url=self._storage.url_for(r["artifact_id"]),
            )
            for r in rows
        ]

    # -- Concrete query methods (not on protocol) -------------------------

    async def query(
        self,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> builtins.list[dict[str, Any]]:
        """Rich metadata query. Not on the protocol — check isinstance first."""
        await self._ensure_initialised()
        rows = await self._index.query_recent(
            agent_id=agent_id,
            thread_id=thread_id,
            tag=tag,
            since=since,
            limit=limit,
        )
        return [dict(r) for r in rows]

    async def count_per_thread(self, thread_ids: builtins.list[str]) -> dict[str, int]:
        """Return artifact count keyed by thread_id."""
        await self._ensure_initialised()
        return await self._index.count_per_thread(thread_ids)
