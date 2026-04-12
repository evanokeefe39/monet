"""Planning graph — iterative work brief construction with HITL approval.

Resume after interrupt via:
    Command(resume={"approved": bool, "feedback": str | None})

Returns an uncompiled StateGraph.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import (
    RunnableConfig,  # noqa: TC002 — needed at runtime for LangGraph signature introspection
)
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from monet import get_catalogue
from monet.core.tracing import attached_trace, extract_carrier_from_config

from ._invoke import invoke_agent
from ._result_parser import ParseFailure, parse_json_output
from ._retry_budget import check_budget, increment_budget
from ._state import PlanningState
from ._validate import _assert_registered

if TYPE_CHECKING:
    from monet.core.hooks import GraphHookRegistry

MAX_REVISIONS = 3


async def planner_node(state: PlanningState, config: RunnableConfig) -> dict[str, Any]:
    """Call planner/plan to produce a work brief."""
    context_entries: list[dict[str, Any]] = []
    for entry in state.get("planning_context") or []:
        context_entries.append(
            {
                "type": "artifact",
                "summary": entry.get("content", ""),
                "content": entry.get("content", ""),
            }
        )
    feedback = state.get("human_feedback")
    if feedback:
        context_entries.append(
            {"type": "instruction", "summary": "Human feedback", "content": feedback}
        )

    async with attached_trace(extract_carrier_from_config(config)):
        result = await invoke_agent(
            "planner",
            command="plan",
            task=state["task"],
            context=context_entries,
            trace_id=state.get("trace_id", ""),
            run_id=state.get("run_id", ""),
        )

    parsed = parse_json_output(result)
    if isinstance(parsed, ParseFailure):
        # Fallback: check catalogue artifacts for the brief.
        if result.artifacts:
            pointer = result.artifacts[0]
            content_bytes, _meta = await get_catalogue().read(pointer["artifact_id"])
            brief: dict[str, Any] = json.loads(content_bytes.decode())
        else:
            brief = {}
    else:
        brief = parsed

    return {"work_brief": brief}


async def human_approval_node(state: PlanningState) -> dict[str, Any]:
    """Interrupt for human approval."""
    brief = state.get("work_brief") or {}
    summary = {
        "goal": brief.get("goal", ""),
        "phases": [p.get("name", "") for p in brief.get("phases", [])],
        "assumptions": brief.get("assumptions", []),
    }
    decision = interrupt(summary)

    approved = decision.get("approved", False) if isinstance(decision, dict) else False
    feedback = decision.get("feedback") if isinstance(decision, dict) else None

    if approved:
        return {"plan_approved": True}
    if feedback and check_budget(state, MAX_REVISIONS):
        return {
            "plan_approved": False,
            "human_feedback": feedback,
            **increment_budget(state),
        }
    return {"plan_approved": False}


def route_from_planner(state: PlanningState) -> str:
    if state.get("work_brief"):
        return "human_approval"
    return END


def route_from_approval(state: PlanningState) -> str:
    if state.get("plan_approved"):
        return END
    if state.get("human_feedback") and check_budget(state, MAX_REVISIONS + 1):
        return "planner"
    return END


def build_planning_graph(
    hooks: GraphHookRegistry | None = None,
) -> StateGraph[PlanningState]:
    """Build the planning graph with HITL approval. Returns uncompiled StateGraph.

    Args:
        hooks: Optional graph hook registry. Fires ``before_planning``
            with the planning state before the planner runs, and
            ``after_planning`` with the work brief after planning.
    """
    _assert_registered("planner", "plan")

    _planner_inner = planner_node

    async def _planner_with_hooks(
        state: PlanningState, config: RunnableConfig
    ) -> dict[str, Any]:
        if hooks:
            state = await hooks.run("before_planning", state)
        update = await _planner_inner(state, config)
        if hooks and update.get("work_brief"):
            update["work_brief"] = await hooks.run(
                "after_planning", update["work_brief"]
            )
        return update

    node = _planner_with_hooks if hooks else planner_node

    graph = StateGraph(PlanningState)
    graph.add_node("planner", node)
    graph.add_node("human_approval", human_approval_node)
    graph.set_entry_point("planner")
    graph.add_conditional_edges(
        "planner", route_from_planner, {"human_approval": "human_approval", END: END}
    )
    graph.add_conditional_edges(
        "human_approval", route_from_approval, {"planner": "planner", END: END}
    )
    return graph
