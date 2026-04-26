"""Chat state schema — structurally inherits PlanningState."""

from __future__ import annotations

from typing import Annotated, Any

from monet.orchestration._state import _append_reducer
from monet.orchestration.prebuilt._state import PlanningState


class ChatState(PlanningState, total=False):
    """State for the chat graph.

    Inherits ``PlanningState`` structurally so the mounted planning
    subgraph shares fields by name — ``task``, ``work_brief_pointer``,
    ``routing_skeleton``, ``plan_approved``, ``revision_count``,
    ``pending_questions``, ``followup_answers``, ``followup_attempts``,
    ``human_feedback``, ``planner_error``, ``planning_context``,
    ``messages``.

    Chat-only fields:

    - ``route``: routing decision written by ``parse_command_node`` or
      ``triage_node``.
    - ``command_meta``: per-route metadata (specialist agent + mode,
      unknown-command sentinel, clarification prompt).

    Execution-subgraph fields (populated by the mounted planning
    subgraph's planner, consumed by the mounted execution subgraph):

    - ``completed_node_ids``, ``wave_results``, ``wave_reflections``.
    """

    route: str | None
    command_meta: dict[str, Any]
    completed_node_ids: list[str]
    wave_results: Annotated[list[dict[str, Any]], _append_reducer]
    wave_reflections: Annotated[list[dict[str, Any]], _append_reducer]
