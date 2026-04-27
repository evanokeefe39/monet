"""Core orchestration state utilities.

Prebuilt state schemas (planning, execution, chat graphs) live in
:mod:`monet.orchestration.prebuilt._state`. This module provides the
primitives any graph author needs:

- ``_append_reducer`` — LangGraph reducer for append-only lists
- ``AgentInvocationResult`` — universal shape for a completed ``invoke_agent()`` call
"""

from __future__ import annotations

from typing import Any, TypedDict

_RESET: list[dict[str, Any]] = []


def _append_reducer(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reducer that appends new entries to existing list.

    Passing the ``_RESET`` sentinel (empty list singleton from this module)
    clears the accumulated state. Normal empty lists created elsewhere
    are identity-compared so ``[]`` from other call sites still appends
    nothing (preserving existing behaviour).
    """
    if new is _RESET:
        return []
    return (existing or []) + new


class AgentInvocationResult(TypedDict):
    """Universal shape for a completed agent invocation.

    Any graph calling ``invoke_agent()`` writes results in this shape.
    ``id`` is caller-assigned — prebuilt sets it to ``RoutingNode.id``,
    custom graphs use whatever invocation identity they need.

    ``output`` and ``artifacts`` are distinct fields. ``output`` is the
    inline result (string or structured dict). ``artifacts`` lists the
    artifact pointers written by the agent.
    """

    id: str
    agent_id: str
    command: str
    output: str | dict[str, Any] | None
    artifacts: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    success: bool
