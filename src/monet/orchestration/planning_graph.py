"""Planning graph — iterative work brief construction with HITL approval.

The orchestrator is pointer-only: planning state carries a ``work_brief_pointer``
(artifact store pointer) and a ``routing_skeleton`` (flat DAG of agent invocations).
The full work brief artifact is never read on the orchestration side — workers
resolve it via the ``inject_plan_context`` hook at invocation time.

Resume after interrupt via:
    Command(resume={"approved": bool, "feedback": str | None})

Returns an uncompiled StateGraph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.runnables import (
    RunnableConfig,  # noqa: TC002 — needed at runtime for LangGraph signature introspection
)
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from opentelemetry import trace
from pydantic import ValidationError

from monet.core.tracing import attached_trace, extract_carrier_from_config
from monet.types import find_artifact

from ._invoke import invoke_agent
from ._retry_budget import check_budget, increment_budget
from ._state import PlanningState, RoutingSkeleton

if TYPE_CHECKING:
    from monet.core.hooks import GraphHookRegistry

MAX_REVISIONS = 3

_tracer = trace.get_tracer("monet.orchestration.planning")


async def planner_node(state: PlanningState, config: RunnableConfig) -> dict[str, Any]:
    """Call planner/plan, store pointer + skeleton. Never read artifact content."""
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

    if not result.success:
        reasons = "; ".join(
            (s.get("reason") or "").splitlines()[0][:200]
            for s in result.signals
            if s.get("reason")
        )
        return {
            "work_brief_pointer": None,
            "routing_skeleton": None,
            "planner_error": (
                f"Planner failed: {reasons}" if reasons else "Planner failed"
            ),
        }

    pointer = find_artifact(result.artifacts, "work_brief")
    if pointer is None:
        return {
            "work_brief_pointer": None,
            "routing_skeleton": None,
            "planner_error": (
                f"Planner did not produce a work_brief artifact. "
                f"{len(result.artifacts)} artifact(s) returned."
            ),
        }

    inline = result.output if isinstance(result.output, dict) else {}

    # Cross-check artifact id reported inline against the keyed artifact.
    reported_id = inline.get("work_brief_artifact_id")
    if reported_id and reported_id != pointer["artifact_id"]:
        return {
            "work_brief_pointer": None,
            "routing_skeleton": None,
            "planner_error": (
                f"Planner reported artifact_id '{reported_id}' "
                f"but keyed artifact has '{pointer['artifact_id']}'."
            ),
        }

    # Validate the routing skeleton from inline output.
    skeleton_raw = inline.get("routing_skeleton")
    if not skeleton_raw:
        return {
            "work_brief_pointer": None,
            "routing_skeleton": None,
            "planner_error": "Planner did not return routing_skeleton in output.",
        }
    try:
        RoutingSkeleton.model_validate(skeleton_raw)
    except ValidationError as exc:
        return {
            "work_brief_pointer": None,
            "routing_skeleton": None,
            "planner_error": f"Routing skeleton invalid: {exc}",
        }

    return {
        "work_brief_pointer": pointer,
        "routing_skeleton": skeleton_raw,
        "planner_error": None,
    }


async def human_approval_node(state: PlanningState) -> dict[str, Any]:
    """Interrupt for human approval.

    Passes both the work brief pointer and the routing skeleton to the
    interrupt payload so UIs can render plan structure without a
    artifact read.
    """
    pointer = state.get("work_brief_pointer")
    if not pointer:
        return {"plan_approved": False}
    decision = interrupt(
        {
            "work_brief_pointer": pointer,
            "routing_skeleton": state.get("routing_skeleton"),
        }
    )

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


async def planning_failed_node(state: PlanningState) -> dict[str, Any]:
    """Terminal node for planning failures — emits OTel span."""
    error = state.get("planner_error") or "Unknown planning failure"
    with _tracer.start_as_current_span(
        "planning.failed",
        attributes={
            "monet.run_id": state.get("run_id", ""),
            "monet.error": error[:500],
        },
    ):
        pass
    return {"plan_approved": False}


def route_from_planner(state: PlanningState) -> str:
    """Exhaustive routing from planner: either approval or failure."""
    if state.get("work_brief_pointer") is None:
        return "planning_failed"
    return "human_approval"


def route_from_approval(state: PlanningState) -> str:
    if state.get("plan_approved"):
        return END
    if state.get("human_feedback") and check_budget(state, MAX_REVISIONS + 1):
        return "planner"
    return END


def build_planning_subgraph(
    hooks: GraphHookRegistry | None = None,
) -> StateGraph[PlanningState]:
    """Build the planning subgraph with HITL approval. Returns uncompiled StateGraph.

    Args:
        hooks: Optional graph hook registry. Fires ``before_planning``
            with the planning state before the planner runs, and
            ``after_planning`` with the planner update after planning.
    """
    _planner_inner = planner_node

    async def _planner_with_hooks(
        state: PlanningState, config: RunnableConfig
    ) -> dict[str, Any]:
        if hooks:
            state = await hooks.run("before_planning", state)
        update = await _planner_inner(state, config)
        if hooks and update.get("routing_skeleton"):
            update = await hooks.run("after_planning", update)
        return update

    node = _planner_with_hooks if hooks else planner_node

    graph = StateGraph(PlanningState)
    graph.add_node("planner", node)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("planning_failed", planning_failed_node)
    graph.set_entry_point("planner")
    graph.add_conditional_edges(
        "planner",
        route_from_planner,
        {
            "human_approval": "human_approval",
            "planning_failed": "planning_failed",
        },
    )
    graph.add_conditional_edges(
        "human_approval", route_from_approval, {"planner": "planner", END: END}
    )
    graph.add_edge("planning_failed", END)
    return graph
