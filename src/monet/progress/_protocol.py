from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monet.events import ProgressEvent

__all__ = ["ProgressReader", "ProgressWriter"]


class ProgressWriter(Protocol):
    async def record(self, run_id: str, event: ProgressEvent) -> int:
        """Append event. Returns assigned event_id. Monotonic within run_id."""
        ...


class ProgressReader(Protocol):
    async def query(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[ProgressEvent]: ...

    def stream(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[ProgressEvent]: ...

    async def has_cause(self, run_id: str, cause_id: str) -> bool: ...

    async def has_decision(self, run_id: str, cause_id: str) -> bool: ...
