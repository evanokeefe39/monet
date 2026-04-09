"""Manifest validation helpers — fail at the earliest detectable point.

Fixed agents required by graph builders are checked at build time so
LangGraph Server fails to start with a loud RuntimeError if a required
agent wasn't declared. Dynamic agents specified by a planner are
checked at fan-out time and surface as SemanticError so HITL can respond.
"""

from __future__ import annotations

from monet._manifest import default_manifest


def _assert_registered(agent_id: str, command: str) -> None:
    """Raise if ``agent_id``/``command`` is not declared in the manifest."""
    if not default_manifest.is_available(agent_id, command):
        msg = (
            f"Required agent '{agent_id}/{command}' is not declared. "
            "Import the agent module or call default_manifest.declare() "
            "before building graphs."
        )
        raise RuntimeError(msg)


__all__ = ["_assert_registered"]
