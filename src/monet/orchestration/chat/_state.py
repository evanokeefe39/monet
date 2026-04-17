"""Chat state schema — structurally inherits PlanningState."""

from __future__ import annotations

from typing import Annotated, Any

from monet.orchestration._state import PlanningState, _append_reducer


def _message_reducer(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append-only reducer for chat messages."""
    return (existing or []) + new


class ChatState(PlanningState, total=False):
    """State for the chat graph.

    Inherits ``PlanningState`` structurally so the mounted planning
    subgraph shares fields by name — ``task``, ``work_brief_pointer``,
    ``routing_skeleton``, ``plan_approved``, ``revision_count``,
    ``pending_questions``, ``followup_answers``, ``followup_attempts``,
    ``human_feedback``, ``planner_error``, ``planning_context``.

    Chat-only fields:

    - ``messages``: append-only transcript.
    - ``route``: routing decision written by ``parse_command_node`` or
      ``triage_node``.
    - ``command_meta``: per-route metadata (specialist agent + mode,
      unknown-command sentinel, clarification prompt).

    Execution-subgraph fields (populated by the mounted planning
    subgraph's planner, consumed by the mounted execution subgraph):

    - ``completed_node_ids``, ``wave_results``, ``wave_reflections``.
    """

    messages: Annotated[list[dict[str, Any]], _message_reducer]
    route: str | None
    command_meta: dict[str, Any]
    completed_node_ids: list[str]
    wave_results: Annotated[list[dict[str, Any]], _append_reducer]
    wave_reflections: Annotated[list[dict[str, Any]], _append_reducer]
