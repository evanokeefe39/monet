"""Internal run-state tracking — not part of the public API.

Caches ``(run_id, graph_id) -> thread_id`` so adapters can re-find
threads without hitting the server's search endpoint every time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _RunStore:
    """In-memory cache of ``run_id -> {graph_id: thread_id}`` for a client."""

    _threads: dict[str, dict[str, str]] = field(default_factory=dict)

    def put_thread(self, run_id: str, graph_id: str, thread_id: str) -> None:
        """Record that *thread_id* is the ``graph_id`` thread for *run_id*."""
        self._threads.setdefault(run_id, {})[graph_id] = thread_id

    def get_thread(self, run_id: str, graph_id: str) -> str | None:
        """Return the cached thread id or ``None``."""
        return self._threads.get(run_id, {}).get(graph_id)

    def threads_for(self, run_id: str) -> dict[str, str]:
        """Return the full ``{graph_id: thread_id}`` mapping for a run."""
        return dict(self._threads.get(run_id, {}))
