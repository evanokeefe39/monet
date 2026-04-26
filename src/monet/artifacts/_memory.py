"""In-memory artifact client for unit tests only. Not for production."""

from __future__ import annotations

from typing import TYPE_CHECKING

from monet.types import ArtifactPointer

if TYPE_CHECKING:
    from monet.artifacts._metadata import ArtifactMetadata


class InMemoryArtifactClient:
    """Dict-backed artifact store for tests. No I/O, no database."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, ArtifactMetadata]] = {}

    async def write(
        self, content: bytes, metadata: ArtifactMetadata
    ) -> ArtifactPointer:
        """Write content and metadata to in-memory store."""
        artifact_id = metadata["artifact_id"]
        self._store[artifact_id] = (content, metadata)
        return ArtifactPointer(
            artifact_id=artifact_id,
            url=f"memory://{artifact_id}",
        )

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Read content and metadata from in-memory store."""
        if artifact_id not in self._store:
            msg = f"Artifact not found: {artifact_id}"
            raise KeyError(msg)
        return self._store[artifact_id]

    async def query_recent(
        self,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[ArtifactMetadata]:
        """Linear scan over the dict-backed store. Test-only."""
        rows = [meta for _, meta in self._store.values()]
        if agent_id is not None:
            rows = [m for m in rows if m.get("agent_id") == agent_id]
        if thread_id is not None:
            rows = [m for m in rows if m.get("thread_id") == thread_id]
        if since is not None:
            rows = [m for m in rows if (m.get("created_at") or "") >= since]
        if tag is not None:
            rows = [m for m in rows if tag in (m.get("tags") or {})]
        rows.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        return rows[:limit]

    async def count_per_thread(self, thread_ids: list[str]) -> dict[str, int]:
        """Return artifact count keyed by thread_id. Test-only."""
        counts: dict[str, int] = {tid: 0 for tid in thread_ids}
        for _, meta in self._store.values():
            tid = meta.get("thread_id")
            if tid in counts:
                counts[tid] += 1  # type: ignore[index]
        return {tid: c for tid, c in counts.items() if c > 0}
