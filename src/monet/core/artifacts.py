"""Artifact store integration for the SDK. Internal.

Public surface: monet.get_artifacts() and monet.artifacts.configure_artifacts().
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
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


# ── ArtifactStoreHandle — returned by get_artifacts() ─────────────────────────


class ArtifactStoreHandle:
    """Returned by get_artifacts(). Provides read/write access to the artifact store.

    write() — writes to the backend AND registers the pointer with the
    decorator's artifact collector. Pointers appear in AgentResult.artifacts.
    Call with await.

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
        pointer = await _artifact_backend.write(
            content=content,
            content_type=content_type,
            summary=summary,
            confidence=confidence,
            completeness=completeness,
            sensitivity_label=sensitivity_label,
            **kwargs,
        )
        # Attach optional semantic key to the pointer.
        key = kwargs.get("key")
        if isinstance(key, str):
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
        query = getattr(_artifact_backend, "query_recent", None)
        if query is None:
            msg = (
                f"{type(_artifact_backend).__name__} does not implement "
                "query_recent(). Use a backend such as ArtifactService that "
                "supports metadata queries."
            )
            raise NotImplementedError(msg)
        result: list[ArtifactMetadata] = await query(
            agent_id=agent_id,
            thread_id=thread_id,
            tag=tag,
            since=since,
            limit=limit,
        )
        return result


_handle_instance = ArtifactStoreHandle()


def get_artifacts() -> ArtifactStoreHandle:
    """Return the context-aware artifact store handle.

    One of the three core SDK getters alongside get_run_context() and
    get_run_logger(). Works anywhere in the call stack beneath an
    @agent decorated function.
    """
    return _handle_instance
