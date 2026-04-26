"""Artifact store integration for the SDK. Internal.

Public surface: monet.get_artifacts() and monet.artifacts.configure_artifacts().
"""

from __future__ import annotations

import hashlib
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet.artifacts._metadata import ArtifactMetadata
    from monet.artifacts._protocol import ArtifactClient
    from monet.types import ArtifactPointer


# ── Backend — configured once at application startup ──────────────────────────

_artifact_backend: ArtifactClient | None = None


def _set_artifact_backend(client: ArtifactClient | None) -> None:
    """Wire an artifact store implementation. Internal — call configure_artifacts()
    from monet.artifacts instead.

    Pass None to reset — useful in tests.
    """
    global _artifact_backend
    if client is not None:
        from monet.artifacts._protocol import ArtifactClient as _Protocol

        if not isinstance(client, _Protocol):
            msg = (
                f"{client!r} does not implement ArtifactClient protocol. "
                "Must have async write() and read() methods."
            )
            raise TypeError(msg)
    _artifact_backend = client


def has_backend() -> bool:
    """True when an artifact backend has been configured."""
    return _artifact_backend is not None


# ── Artifact collector — set by decorator before each invocation ──────────────

_artifact_collector: ContextVar[list[Any] | None] = ContextVar(
    "_artifact_collector", default=None
)

# Parallel sidecar — sha256 hex of each explicit artifact write from within
# an @agent call, used by _wrap_result to suppress the auto-offload when the
# agent already persisted the exact bytes it is returning. Kept separate from
# _artifact_collector so the public artifacts list stays list[ArtifactPointer].
_artifact_hashes: ContextVar[set[str] | None] = ContextVar(
    "_artifact_hashes", default=None
)


# ── ArtifactStore — returned by get_artifacts() ───────────────────────────────


class ArtifactStore:
    """Returned by get_artifacts(). Provides read/write access to the artifact store.

    write() — resolves run context, builds ArtifactMetadata, writes to the
    backend, and registers the pointer with the decorator's artifact collector.
    Pointers appear in AgentResult.artifacts. Call with await.

    read() — fetches content from the backend. No side effects.
    No registration, no collection. Pure data retrieval. Call with await.

    Reads _artifact_backend from the module global on every call so
    configure_artifacts() takes effect immediately for the existing singleton.
    """

    async def write(
        self,
        content: bytes,
        content_type: str,
        summary: str,
        confidence: float,
        completeness: str,
        sensitivity_label: str = "internal",
        **kwargs: Any,
    ) -> ArtifactPointer:
        if _artifact_backend is None:
            msg = (
                "get_artifacts() requires an artifact store backend. "
                "Call monet.artifacts.configure_artifacts(ArtifactService(...)) "
                "at startup. In tests: configure_artifacts(InMemoryArtifactClient())."
            )
            raise NotImplementedError(msg)

        # Resolve run context — optional, can be called outside @agent.
        run_id: str | None = None
        trace_id: str | None = None
        agent_id: str | None = None
        thread_id: str | None = None
        try:
            from monet.core.context import get_run_context

            ctx = get_run_context()
            run_id = ctx.get("run_id")
            trace_id = ctx.get("trace_id")
            agent_id = ctx.get("agent_id")
            thread_id = ctx.get("thread_id")  # type: ignore[typeddict-item]
        except (LookupError, RuntimeError):
            pass

        key: str | None = str(kwargs["key"]) if "key" in kwargs else None
        # Stable, run-scoped id when a semantic key is provided.
        artifact_id = f"{run_id}--{key}" if key and run_id else str(uuid.uuid4())

        from monet.artifacts._metadata import ArtifactMetadata

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
            tags=dict(kwargs["tags"]) if "tags" in kwargs else {},  # type: ignore[call-overload]
            created_at=datetime.now(tz=UTC).isoformat(),
        )

        pointer = await _artifact_backend.write(content, metadata)
        if key:
            pointer["key"] = key

        collector = _artifact_collector.get()
        if collector is not None:
            collector.append(pointer)
        hashes = _artifact_hashes.get()
        if hashes is not None:
            hashes.add(hashlib.sha256(content).hexdigest())
        return pointer

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Fetch content from the backend. Pure pass-through, no side effects."""
        if _artifact_backend is None:
            msg = (
                "get_artifacts() requires an artifact store backend. "
                "Call monet.artifacts.configure_artifacts(ArtifactService(...)) "
                "at startup."
            )
            raise NotImplementedError(msg)
        return await _artifact_backend.read(artifact_id)

    async def query_recent(
        self,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[ArtifactMetadata]:
        """List recent artifact metadata — passes through to backend.

        Backends that implement only ``read``/``write`` raise
        ``NotImplementedError`` for this call.
        """
        if _artifact_backend is None:
            msg = (
                "get_artifacts() requires an artifact store backend. "
                "Call monet.artifacts.configure_artifacts(ArtifactService(...)) "
                "at startup."
            )
            raise NotImplementedError(msg)
        from monet.artifacts._protocol import ArtifactQueryable

        if not isinstance(_artifact_backend, ArtifactQueryable):
            msg = (
                f"{type(_artifact_backend).__name__} does not implement "
                "query_recent(). Use a backend such as ArtifactService that "
                "supports metadata queries."
            )
            raise NotImplementedError(msg)
        return await _artifact_backend.query_recent(
            agent_id=agent_id,
            thread_id=thread_id,
            tag=tag,
            since=since,
            limit=limit,
        )

    async def count_per_thread(self, thread_ids: list[str]) -> dict[str, int]:
        """Return artifact count keyed by thread_id for the given IDs."""
        if _artifact_backend is None:
            return {}
        from monet.artifacts._protocol import ArtifactQueryable

        if not isinstance(_artifact_backend, ArtifactQueryable):
            return {}
        return await _artifact_backend.count_per_thread(thread_ids)


# Keep old name as alias for backwards compatibility.
ArtifactStoreHandle = ArtifactStore

_store_instance = ArtifactStore()


def get_artifacts() -> ArtifactStore:
    """Return the context-aware artifact store handle.

    One of the three core SDK getters alongside get_run_context() and
    get_run_logger(). Works anywhere in the call stack beneath an
    @agent decorated function.
    """
    return _store_instance
