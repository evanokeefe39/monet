"""Artifact backend protocols.

Three types, three methods. Backend extracts whatever it needs from
kwargs on write. Read returns bytes + whatever metadata the backend has
as a plain dict. List returns pointers only — clients that need richer
metadata call backend-specific query methods directly (isinstance-check
against the concrete implementation they expect).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from monet.types import ArtifactPointer


@runtime_checkable
class ArtifactWriter(Protocol):
    """Write-only artifact backend contract."""

    async def write(self, content: bytes, **kwargs: Any) -> ArtifactPointer:
        """Persist content; return a pointer. Backend owns kwargs interpretation."""
        ...


@runtime_checkable
class ArtifactReader(Protocol):
    """Read-only artifact backend contract."""

    async def read(self, artifact_id: str) -> tuple[bytes, dict[str, Any]]:
        """Retrieve content and metadata by artifact_id."""
        ...

    async def list(
        self, *, limit: int = 100, cursor: str | None = None
    ) -> list[ArtifactPointer]:
        """Enumerate artifact pointers. cursor is implementation-defined."""
        ...


@runtime_checkable
class ArtifactClient(ArtifactReader, ArtifactWriter, Protocol):
    """Minimum backend contract: read + write + list."""

    ...


@runtime_checkable
class ArtifactQueryable(Protocol):
    """Optional query capability — backends that support filtered listing.

    Separate from ArtifactClient (ISP): InMemoryArtifactClient should not
    need to implement query. Checked via isinstance at the route layer to
    dispatch to backends that support it; absent backends get empty results.
    """

    async def query(
        self,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...
