"""Chat state schema — structurally inherits PlanningState."""

from __future__ import annotations

from typing import Any

from monet.orchestration.prebuilt._state import PlanningState


class ChatState(PlanningState, total=False):
    """State for the chat graph.

    Inherits ``PlanningState`` structurally so the mounted planning
    subgraph shares fields by name — ``task``, ``work_brief_pointer``,
    ``routing_skeleton``, ``plan_approved``, ``revision_count``,
    ``pending_questions``, ``followup_answers``, ``followup_attempts``,
    ``human_feedback``, ``planner_error``, ``planning_context``,
    ``messages``.

    Execution-subgraph transient state (``wave_results``,
    ``wave_reflections``, ``completed_node_ids``) is scoped to
    ``ExecutionState`` — it never enters ``ChatState``.  The execution
    summary flows back as a ``messages`` entry via subgraph name-matching.

    Chat-only fields:

    - ``route``: routing decision written by ``parse_command_node`` or
      ``triage_node``.
    - ``command_meta``: per-route metadata (specialist agent + mode,
      unknown-command sentinel, clarification prompt).
    """

    route: str | None
    command_meta: dict[str, Any]
