"""Catalogue integration for the SDK. Internal.

Public surface: monet.get_catalogue() and monet.catalogue.configure_catalogue().
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet.catalogue._metadata import ArtifactMetadata
    from monet.catalogue._protocol import CatalogueClient
    from monet.types import ArtifactPointer


# ── Backend — configured once at application startup ──────────────────────────

_catalogue_backend: CatalogueClient | None = None


def _set_catalogue_backend(client: CatalogueClient | None) -> None:
    """Wire a catalogue implementation. Internal — call configure_catalogue()
    from monet.catalogue instead.

    Pass None to reset — useful in tests.
    """
    global _catalogue_backend
    if client is not None:
        from monet.catalogue._protocol import CatalogueClient as _Protocol

        if not isinstance(client, _Protocol):
            msg = (
                f"{client!r} does not implement CatalogueClient protocol. "
                "Must have async write() and read() methods."
            )
            raise TypeError(msg)
    _catalogue_backend = client


# ── Artifact collector — set by decorator before each invocation ──────────────

_artifact_collector: ContextVar[list[Any] | None] = ContextVar(
    "_artifact_collector", default=None
)

# Parallel sidecar — sha256 hex of each explicit catalogue write from within
# an @agent call, used by _wrap_result to suppress the auto-offload when the
# agent already persisted the exact bytes it is returning. Kept separate from
# _artifact_collector so the public artifacts list stays list[ArtifactPointer].
_artifact_hashes: ContextVar[set[str] | None] = ContextVar(
    "_artifact_hashes", default=None
)


# ── CatalogueHandle — returned by get_catalogue() ─────────────────────────────


class CatalogueHandle:
    """Returned by get_catalogue(). Provides read/write access to the catalogue.

    write() — writes to the backend AND registers the pointer with the
    decorator's artifact collector. Pointers appear in AgentResult.artifacts.
    Call with await.

    read() — fetches content from the backend. No side effects.
    No registration, no collection. Pure data retrieval. Call with await.

    Reads _catalogue_backend from the module global on every call so
    configure_catalogue() takes effect immediately for the existing singleton.
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
        if _catalogue_backend is None:
            msg = (
                "get_catalogue() requires a catalogue backend. "
                "Call monet.catalogue.configure_catalogue(CatalogueService(...)) "
                "at startup. In tests: configure_catalogue(InMemoryCatalogueClient())."
            )
            raise NotImplementedError(msg)
        pointer = await _catalogue_backend.write(
            content=content,
            content_type=content_type,
            summary=summary,
            confidence=confidence,
            completeness=completeness,
            sensitivity_label=sensitivity_label,
            **kwargs,
        )
        collector = _artifact_collector.get()
        if collector is not None:
            collector.append(pointer)
        hashes = _artifact_hashes.get()
        if hashes is not None:
            hashes.add(hashlib.sha256(content).hexdigest())
        return pointer

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        """Fetch content from the backend. Pure pass-through, no side effects."""
        if _catalogue_backend is None:
            msg = (
                "get_catalogue() requires a catalogue backend. "
                "Call monet.catalogue.configure_catalogue(CatalogueService(...)) "
                "at startup."
            )
            raise NotImplementedError(msg)
        return await _catalogue_backend.read(artifact_id)


_handle_instance = CatalogueHandle()


def get_catalogue() -> CatalogueHandle:
    """Return the context-aware catalogue handle.

    One of the three core SDK getters alongside get_run_context() and
    get_run_logger(). Works anywhere in the call stack beneath an
    @agent decorated function.
    """
    return _handle_instance
