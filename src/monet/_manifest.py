"""Agent capability manifest — static declaration of available agents.

The manifest answers "what's available?" for the orchestration layer.
It is separate from the handler registry ("how to execute?") which
lives on the worker side.

Populated by the ``@agent`` decorator at decoration time, or by
explicit ``declare()`` calls for remote agents.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from collections.abc import Generator

__all__ = ["AgentCapability", "AgentManifest", "default_manifest"]


class AgentCapability(TypedDict):
    """Declared capability of an agent."""

    agent_id: str
    command: str
    description: str
    pool: str


class AgentManifest:
    """Static registry of agent capabilities.

    Thread-safe via Lock. Populated at decoration/startup time.
    """

    def __init__(self) -> None:
        self._capabilities: dict[tuple[str, str], AgentCapability] = {}
        self._lock = threading.Lock()

    def declare(
        self,
        agent_id: str,
        command: str,
        description: str = "",
        pool: str = "local",
    ) -> None:
        """Declare that an agent capability exists."""
        with self._lock:
            self._capabilities[(agent_id, command)] = AgentCapability(
                agent_id=agent_id,
                command=command,
                description=description,
                pool=pool,
            )

    def get_pool(self, agent_id: str, command: str) -> str | None:
        """Return the pool for a declared capability, or None."""
        cap = self._capabilities.get((agent_id, command))
        return cap["pool"] if cap else None

    def is_available(self, agent_id: str, command: str) -> bool:
        """Check if a capability has been declared."""
        return (agent_id, command) in self._capabilities

    def capabilities(self) -> list[AgentCapability]:
        """Return all declared capabilities."""
        with self._lock:
            return list(self._capabilities.values())

    def clear(self) -> None:
        """Remove all declarations."""
        with self._lock:
            self._capabilities.clear()

    @contextlib.contextmanager
    def manifest_scope(self) -> Generator[None]:
        """Context manager that snapshots and restores manifest state.

        Use in tests to isolate capability declarations.
        """
        with self._lock:
            snapshot = dict(self._capabilities)
        try:
            yield
        finally:
            with self._lock:
                self._capabilities = snapshot


default_manifest = AgentManifest()
