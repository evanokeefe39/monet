"""Planning graph — iterative plan construction with HITL approval.

Nodes:
  planner_node — calls sm-planner/plan to build a work brief
  research_node — calls sm-researcher/fast for additional context
  human_approval_node — calls interrupt() for terminal-based approval

Routing is deterministic — no LLM makes routing decisions.
Resume after interrupt via Command(resume={"approved": bool, "feedback": str|None}).
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from monet.orchestration import invoke_agent

from .state import PlanningState

MAX_REVISIONS = 3


async def planner_node(state: PlanningState) -> dict[str, Any]:
    """Call sm-planner/plan to produce a work brief.

    If human_feedback is present, passes it as a context entry so the
    planner picks a revision variant.
    """
    context_entries: list[dict[str, Any]] = []

    # Add accumulated research as artifact context entries
    for entry in state.get("planning_context") or []:
        context_entries.append(
            {
                "type": "artifact",
                "summary": entry.get("content", ""),
                "content": entry.get("content", ""),
            }
        )

    # Add human feedback as an instruction context entry
    feedback = state.get("human_feedback")
    if feedback:
        context_entries.append(
            {
                "type": "instruction",
                "summary": "Human feedback",
                "content": feedback,
            }
        )

    result = await invoke_agent(
        "sm-planner",
        command="plan",
        task=state["user_message"],
        context=context_entries,
        trace_id=state.get("trace_id", ""),
        run_id=state.get("run_id", ""),
    )
    brief = json.loads(result.output) if isinstance(result.output, str) else {}
    return {"work_brief": brief}


async def research_node(state: PlanningState) -> dict[str, Any]:
    """Call sm-researcher/fast and append to planning_context."""
    result = await invoke_agent(
        "sm-researcher",
        command="fast",
        task=state["user_message"],
        trace_id=state.get("trace_id", ""),
        run_id=state.get("run_id", ""),
    )
    entry = {
        "type": "research",
        "content": result.output if isinstance(result.output, str) else "",
    }
    return {"planning_context": [entry]}


async def human_approval_node(state: PlanningState) -> dict[str, Any]:
    """Interrupt for human approval of the work brief.

    Displays: goal, phase names, assumptions, quality_criteria.
    Resume with: {"approved": bool, "feedback": str | None}
    """
    brief = state.get("work_brief") or {}
    summary = {
        "goal": brief.get("goal", ""),
        "phases": [p.get("name", "") for p in brief.get("phases", [])],
        "assumptions": brief.get("assumptions", []),
        "quality_criteria": brief.get("quality_criteria", {}),
    }

    decision = interrupt(summary)

    approved = decision.get("approved", False) if isinstance(decision, dict) else False
    feedback = decision.get("feedback") if isinstance(decision, dict) else None

    if approved:
        return {"plan_approved": True}

    if feedback and state.get("revision_count", 0) < MAX_REVISIONS:
        return {
            "plan_approved": False,
            "human_feedback": feedback,
            "revision_count": state.get("revision_count", 0) + 1,
        }

    # Rejected without feedback or revision limit reached
    return {"plan_approved": False}


# ---------------------------------------------------------------------------
# Routing functions — deterministic, read structured state only
# ---------------------------------------------------------------------------


def route_from_planner(state: PlanningState) -> str:
    """Route after planner produces a work brief."""
    if state.get("work_brief"):
        return "human_approval"
    return END


def route_from_approval(state: PlanningState) -> str:
    """Route after human approval decision."""
    if state.get("plan_approved"):
        return END
    if state.get("human_feedback") and state.get("revision_count", 0) <= MAX_REVISIONS:
        return "planner"
    return END


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_planning_graph() -> StateGraph:
    """Build the planning graph with HITL approval gate."""
    graph = StateGraph(PlanningState)

    graph.add_node("planner", planner_node)
    graph.add_node("research", research_node)
    graph.add_node("human_approval", human_approval_node)

    graph.set_entry_point("planner")

    graph.add_conditional_edges(
        "planner",
        route_from_planner,
        {"human_approval": "human_approval", END: END},
    )
    graph.add_edge("research", "planner")
    graph.add_conditional_edges(
        "human_approval",
        route_from_approval,
        {"planner": "planner", END: END},
    )

    return graph
