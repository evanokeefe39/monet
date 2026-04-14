"""Shared fake LangGraph SDK client for client / adapter tests.

Centralises the minimal surface used by ``MonetClient`` and the
default-pipeline adapter so test files don't duplicate the scaffolding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _FakeChunk:
    event: str = "updates"
    data: Any = None


class _FakeThreads:
    def __init__(self, states: list[dict[str, Any]]) -> None:
        self._states = list(states)
        self._next_nodes: list[list[str]] = [[] for _ in states]
        self._state_idx: dict[str, int] = {}
        self._thread_metadata: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._search_threads: list[dict[str, Any]] = []

    def set_next(self, per_drain: list[list[str]]) -> None:
        self._next_nodes = per_drain

    def set_search_results(self, threads: list[dict[str, Any]]) -> None:
        self._search_threads = threads

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        self._counter += 1
        tid = f"t-{self._counter}"
        idx = self._counter - 1
        self._state_idx[tid] = idx
        meta = kwargs.get("metadata") or {}
        self._thread_metadata[tid] = meta
        return {"thread_id": tid, "metadata": meta}

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        idx = self._state_idx.get(thread_id, 0)
        if idx >= len(self._states):
            return {"values": {}, "next": []}
        return {
            "values": self._states[idx],
            "next": self._next_nodes[idx] if idx < len(self._next_nodes) else [],
        }

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._search_threads

    async def update(self, thread_id: str, **kwargs: Any) -> None:
        self._thread_metadata[thread_id].update(kwargs.get("metadata") or {})


class _FakeRuns:
    def __init__(self, chunks_per_stream: list[list[_FakeChunk]] | None = None) -> None:
        self._chunks = chunks_per_stream or []
        self._stream_count = 0

    def stream(
        self,
        thread_id: str,
        graph_id: str,
        **kwargs: Any,
    ) -> Any:
        chunks = (
            self._chunks[self._stream_count]
            if self._stream_count < len(self._chunks)
            else []
        )
        self._stream_count += 1

        async def _gen() -> Any:
            for chunk in chunks:
                yield chunk

        return _gen()


@dataclass
class _FakeAssistants:
    entries: list[dict[str, Any]] = field(default_factory=list)

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.entries


class _FakeLangGraphClient:
    """Minimal LangGraph SDK stand-in — add only what tests need."""

    def __init__(
        self,
        states: list[dict[str, Any]],
        chunks: list[list[_FakeChunk]] | None = None,
    ) -> None:
        self.threads = _FakeThreads(states)
        self.runs = _FakeRuns(chunks)
        self.assistants = _FakeAssistants()
