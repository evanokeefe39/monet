"""Artifact store integration for the SDK. Internal.

Public surface: monet.get_artifacts() and monet.artifacts.configure_artifacts().
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
                "Must have async write(), read(), and list() methods."
            )
            raise TypeError(msg)
    _artifact_backend = client


def has_backend() -> bool:
    """True when an artifact backend has been configured."""
    return _artifact_backend is not None


def get_artifact_backend() -> ArtifactClient | None:
    """Return the configured backend directly. Server/admin code only.

    Prefer get_artifacts() for agent-side use. This accessor exists for
    server routes that need to isinstance-check for backend-specific
    query methods not on the protocol.
    """
    return _artifact_backend


# ── Artifact collector — set by decorator before each invocation ──────────────

_artifact_collector: ContextVar[list[Any] | None] = ContextVar(
    "_artifact_collector", default=None
)

# Parallel sidecar — sha256 hex of each explicit artifact write from within
# an @agent call, used by _wrap_result to suppress the auto-offload when the
# agent already persisted the exact bytes it is returning.
_artifact_hashes: ContextVar[set[str] | None] = ContextVar(
    "_artifact_hashes", default=None
)


# ── ArtifactStore — returned by get_artifacts() ───────────────────────────────


class ArtifactStore:
    """Returned by get_artifacts(). Provides read/write/list access to the artifact
    store.

    write() — resolves run context, passes all through to the backend as
    kwargs, and registers the returned pointer with the decorator's artifact
    collector. Pointers appear in AgentResult.artifacts.

    read() — pure pass-through to backend. No registration, no collection.

    list() — pure pass-through to backend.

    Reads _artifact_backend from the module global on every call so
    configure_artifacts() takes effect immediately for the existing singleton.
    """

    async def write(
        self,
        content: bytes,
        content_type: str,
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
        agent_run_ctxt: Any = None
        try:
            from monet.core.context import get_run_context

            agent_run_ctxt = get_run_context()
        except (LookupError, RuntimeError):
            pass

        pointer = await _artifact_backend.write(
            content,
            content_type=content_type,
            agent_run_ctxt=agent_run_ctxt,
            **kwargs,
        )

        collector = _artifact_collector.get()
        if collector is not None:
            collector.append(pointer)
        hashes = _artifact_hashes.get()
        if hashes is not None:
            hashes.add(hashlib.sha256(content).hexdigest())
        return pointer

    async def read(self, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
        """Fetch content from the backend. Pure pass-through, no side effects."""
        if _artifact_backend is None:
            msg = (
                "get_artifacts() requires an artifact store backend. "
                "Call monet.artifacts.configure_artifacts(ArtifactService(...)) "
                "at startup."
            )
            raise NotImplementedError(msg)
        return await _artifact_backend.read(artifact_id)

    async def list(
        self, *, limit: int = 100, cursor: str | None = None
    ) -> list[ArtifactPointer]:
        """List artifact pointers. Pure pass-through to backend."""
        if _artifact_backend is None:
            return []
        return await _artifact_backend.list(limit=limit, cursor=cursor)


_store_instance = ArtifactStore()


def get_artifacts() -> ArtifactStore:
    """Return the context-aware artifact store handle.

    One of the three core SDK getters alongside get_run_context() and
    get_run_logger(). Works anywhere in the call stack beneath an
    @agent decorated function.
    """
    return _store_instance
