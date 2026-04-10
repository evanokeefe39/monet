"""Internal run-state tracking — not part of the public API."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _RunState:
    """Maps a monet *run_id* to the LangGraph thread IDs for each phase."""

    run_id: str
    # triaging | planning | executing | interrupted | complete | failed
    status: str = "triaging"
    phase: str = "entry"  # entry | planning | execution
    entry_thread: str | None = None
    planning_thread: str | None = None
    execution_thread: str | None = None


@dataclass
class _RunStore:
    """In-memory cache of run states for a ``MonetClient`` instance."""

    _runs: dict[str, _RunState] = field(default_factory=dict)

    def get(self, run_id: str) -> _RunState | None:
        return self._runs.get(run_id)

    def put(self, state: _RunState) -> None:
        self._runs[state.run_id] = state
