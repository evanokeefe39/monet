"""Agent registry — maps (agent_id, command) pairs to callable handlers.

Thread-safe via Lock. Async-safe because registrations happen at
decoration time (synchronous), not at call time.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, overload

if TYPE_CHECKING:
    from collections.abc import Callable, Generator


class RegisteredAgent(NamedTuple):
    """A registered (agent_id, command) pair with its docstring summary.

    Returned by ``AgentRegistry.registered_agents(with_docstrings=True)``.
    The ``description`` is the first line of the handler's docstring, stripped
    of whitespace, or an empty string if the handler has no docstring.
    """

    agent_id: str
    command: str
    description: str


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

    def exists(self, agent_id: str, command: str) -> bool:
        """Quick safety check — return True if (agent_id, command) is registered."""
        return (agent_id, command) in self._handlers

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

    @overload
    def registered_agents(
        self, *, with_docstrings: Literal[False] = False
    ) -> list[tuple[str, str]]: ...

    @overload
    def registered_agents(
        self, *, with_docstrings: Literal[True]
    ) -> list[RegisteredAgent]: ...

    def registered_agents(
        self, *, with_docstrings: bool = False
    ) -> list[tuple[str, str]] | list[RegisteredAgent]:
        """Return all registered agents.

        Default form returns ``list[tuple[agent_id, command]]`` for
        backwards-compatible iteration. Pass ``with_docstrings=True`` to
        receive ``list[RegisteredAgent]`` triples that also include the
        first line of each handler's docstring as a capability description —
        useful when rendering an agent roster for an LLM planner prompt.
        """
        with self._lock:
            pairs = list(self._handlers.items())
        if not with_docstrings:
            return [key for key, _fn in pairs]
        rows: list[RegisteredAgent] = []
        for (agent_id, command), fn in pairs:
            doc = (fn.__doc__ or "").strip().split("\n", 1)[0]
            rows.append(RegisteredAgent(agent_id, command, doc))
        return rows


default_registry = AgentRegistry()
