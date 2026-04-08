"""Registry validation helpers — fail at the earliest detectable point.

Fixed agents required by graph builders are checked at build time so
LangGraph Server fails to start with a loud RuntimeError if a required
agent module wasn't imported. Dynamic agents specified by a planner are
checked at fan-out time and surface as SemanticError so HITL can respond.
"""

from __future__ import annotations

from monet._registry import default_registry


def _assert_registered(agent_id: str, command: str) -> None:
    """Raise RuntimeError if ``agent_id``/``command`` is not registered."""
    if default_registry.lookup(agent_id, command) is None:
        msg = (
            f"Required agent '{agent_id}/{command}' is not registered. "
            "Import the agent module before building graphs. "
            "Example: import monet.agents"
        )
        raise RuntimeError(msg)


__all__ = ["_assert_registered"]
