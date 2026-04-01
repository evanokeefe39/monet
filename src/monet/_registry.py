"""Agent registry — maps (agent_id, command) pairs to callable handlers.

Thread-safe via Lock. Async-safe because registrations happen at
decoration time (synchronous), not at call time.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

if TYPE_CHECKING:
    from collections.abc import Callable


class AgentRegistry:
    """Registry mapping (agent_id, command) to decorated handler functions."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], Callable[..., Any]] = {}
        self._lock = threading.Lock()

    def register(self, agent_id: str, command: str, fn: Callable[..., Any]) -> None:
        """Register a handler for an agent_id + command pair."""
        with self._lock:
            self._handlers[(agent_id, command)] = fn

    def lookup(self, agent_id: str, command: str) -> Callable[..., Any] | None:
        """Look up a handler. Returns None if not found."""
        return self._handlers.get((agent_id, command))

    def clear(self) -> None:
        """Remove all registrations. Restores empty state."""
        with self._lock:
            self._handlers.clear()

    @contextlib.contextmanager
    def registry_scope(self) -> Generator[None]:
        """Context manager that snapshots and restores registry state.

        Use in tests to isolate registrations:
            with registry.registry_scope():
                @agent(agent_id="test-agent")
                async def my_agent(task: str) -> str: ...
            # registry is restored to pre-scope state here
        """
        with self._lock:
            snapshot = dict(self._handlers)
        try:
            yield
        finally:
            with self._lock:
                self._handlers = snapshot

    def registered_agents(self) -> list[tuple[str, str]]:
        """Return list of registered (agent_id, command) pairs."""
        return list(self._handlers.keys())


default_registry = AgentRegistry()
