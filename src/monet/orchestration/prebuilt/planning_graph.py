"""Planning graph — iterative work brief construction with HITL approval.

The orchestrator is pointer-only: planning state carries a ``work_brief_pointer``
(artifact store pointer) and a ``routing_skeleton`` (flat DAG of agent invocations).
The full work brief artifact is never read on the orchestration side — workers
resolve it via the ``inject_plan_context`` hook at invocation time.

Parameterised by ``max_followup_attempts``:

- ``0`` (default, pipeline): planner questions are treated as failure.
  Pipeline callers (``monet run``) cannot supply answers mid-stream, so
  the planner is expected to plan from first call or fail cleanly.
- ``>=1`` (chat): a questionnaire node interrupts with the questions,
  answers feed back into ``planning_context``, and the planner is
  re-invoked. After ``max_followup_attempts`` rounds the planner is
  force-planned — a best-effort plan is requested regardless of
  remaining ambiguity.

Resume shapes:
- Plan approval: ``{"action": "approve"|"revise"|"reject", "feedback": str|None}``
- Questionnaire: ``{"q0": "answer", "q1": "skip", ...}``

Returns an uncompiled StateGraph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Hashable

    from monet.core.hooks import GraphHookRegistry

from langchain_core.runnables import (
    RunnableConfig,  # noqa: TC002 — needed at runtime for LangGraph signature introspection
)
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from opentelemetry import trace

from monet.core.tracing import attached_trace, extract_carrier_from_config
from monet.orchestration._invoke import invoke_agent
from monet.orchestration._retry_budget import check_budget, increment_budget

from ._forms import build_plan_approval_form, parse_approval_decision
from ._planner_outcome import (
    PlannerFailure,
    PlanOutcome,
    QuestionsOutcome,
    classify_planner_result,
)
from ._state import PlanningState

MAX_REVISIONS = 3

_tracer = trace.get_tracer("monet.orchestration.planning")


def _roster_context_entry() -> dict[str, Any] | None:
    """Snapshot the full-fleet roster from the server-side capability index.

    Orchestration runs in the server process, which holds the authoritative
    ``CapabilityIndex`` populated by worker heartbeats. The planner agent
    runs inside a worker — its local registry only sees its own pool. We
    therefore pass the fleet-wide roster through the planner's task
    context so it can plan across pools.

    Returns ``None`` when no capability index is configured (e.g. unit
    tests); the planner agent then falls back to its local registry.
    """
    from monet.orchestration._invoke import get_capability_index

    index = get_capability_index()
    if index is None:
        return None
    caps = index.capabilities()
    if not caps:
        return None
    agents = [
        {
            "agent_id": cap["agent_id"],
            "command": cap["command"],
            "description": cap.get("description", ""),
        }
        for cap in caps
    ]
    return {
        "type": "agent_roster",
        "summary": f"{len(agents)} agent(s) available across the fleet",
        "agents": agents,
    }


def _build_context(state: PlanningState, *, force_plan: bool) -> list[dict[str, Any]]:
    """Assemble planner context from planning_context + feedback + force flag."""
    entries: list[dict[str, Any]] = []
    for entry in state.get("planning_context") or []:
        entries.append(
            {
                "type": entry.get("type") or "artifact",
                "summary": entry.get("summary", ""),
                "content": entry.get("content", ""),
            }
        )
    feedback = state.get("human_feedback")
    if feedback:
        entries.append(
            {"type": "instruction", "summary": "Human feedback", "content": feedback}
        )
    for answer in state.get("followup_answers") or []:
        entries.append(answer)
    if force_plan:
        entries.append(
            {
                "type": "instruction",
                "summary": "Force-plan override",
                "content": (
                    "Produce a best-effort plan now — do NOT return more "
                    "questions. If any parameter is still unknown, pick a "
                    "reasonable default and note it in `assumptions`."
                ),
            }
        )
    return entries


async def _invoke_planner(
    state: PlanningState, config: RunnableConfig, *, force_plan: bool
) -> dict[str, Any]:
    """Invoke planner agent and classify outcome into state patch fields."""
    context_entries = _build_context(state, force_plan=force_plan)
    roster_entry = _roster_context_entry()
    if roster_entry is not None:
        context_entries = [*context_entries, roster_entry]
    configurable = (config or {}).get("configurable") or {}
    tid = configurable.get("thread_id")
    thread_id: str | None = tid if isinstance(tid, str) else None
    # Source run_id from LangGraph config so progress events and lifecycle
    # events carry the same ID that list_thread_runs returns, enabling
    # get_thread_progress to match stored events after thread reopen.
    lg_run_id = str(configurable.get("run_id") or state.get("run_id", ""))
    async with attached_trace(extract_carrier_from_config(config)):
        result = await invoke_agent(
            "planner",
            command="plan",
            task=state["task"],
            context=context_entries,
            trace_id=state.get("trace_id", ""),
            run_id=lg_run_id,
            thread_id=thread_id,
        )

    cleared: dict[str, Any] = {
        "human_feedback": None,
        "followup_answers": None,
    }
    artifacts = [dict(a) for a in result.artifacts]
    outcome = classify_planner_result(result)
    if isinstance(outcome, PlanOutcome):
        return {
            "work_brief_pointer": outcome.work_brief_pointer,
            "routing_skeleton": outcome.routing_skeleton,
            "planning_artifacts": artifacts,
            "planner_error": None,
            "pending_questions": None,
            **cleared,
        }
    if isinstance(outcome, QuestionsOutcome):
        return {
            "work_brief_pointer": None,
            "routing_skeleton": None,
            "planning_artifacts": [],
            "planner_error": None,
            "pending_questions": outcome.questions,
            **cleared,
        }
    assert isinstance(outcome, PlannerFailure)
    return {
        "work_brief_pointer": None,
        "routing_skeleton": None,
        "planning_artifacts": [],
        "planner_error": outcome.reason,
        "pending_questions": None,
        **cleared,
    }


def _make_planner_node(
    max_followup_attempts: int,
) -> Callable[[PlanningState, RunnableConfig], Awaitable[dict[str, Any]]]:
    """Return a planner node closure bound to a follow-up budget."""

    async def planner_node(
        state: PlanningState, config: RunnableConfig
    ) -> dict[str, Any]:
        attempts = state.get("followup_attempts") or 0
        force_plan = attempts >= max_followup_attempts
        return await _invoke_planner(state, config, force_plan=force_plan)

    return planner_node


# Legacy module-level binding: equivalent to ``max_followup_attempts=0``.
# Preserved so existing imports (``default_graph``, tests) keep working.
async def planner_node(state: PlanningState, config: RunnableConfig) -> dict[str, Any]:
    """Call planner/plan, store pointer + skeleton. Never read artifact content."""
    return await _invoke_planner(state, config, force_plan=False)


async def questionnaire_node(state: PlanningState) -> dict[str, Any]:
    """Render pending planner questions as a form, interrupt, collect answers.

    Follow-up attempts are bumped so the planner can detect the
    force-plan condition on the next visit.
    """
    questions = list(state.get("pending_questions") or [])
    attempts = state.get("followup_attempts") or 0
    if not questions:
        return {"pending_questions": None, "followup_attempts": attempts}

    decision = interrupt(_followup_form(questions))

    answers: list[dict[str, Any]] = []
    msg_parts: list[str] = []
    if isinstance(decision, dict):
        for idx, question in enumerate(questions):
            raw = decision.get(f"q{idx}")
            if raw is None:
                continue
            text = str(raw).strip()
            if not text or text.lower() in {"skip", "__skip__", "n/a", "-"}:
                continue
            answers.append(
                {
                    "type": "user_clarification",
                    "summary": question,
                    "content": f"Q: {question}\nA: {text}",
                }
            )
            msg_parts.append(f"q{idx}={text}")

    msg_content = ", ".join(msg_parts) or "(submitted)"
    return {
        "pending_questions": None,
        "followup_attempts": attempts + 1,
        "followup_answers": answers,
        "messages": [{"role": "user", "content": msg_content}],
    }


def _followup_form(questions: list[str]) -> dict[str, Any]:
    """Form-schema payload for the planner's follow-up questionnaire."""
    fields: list[dict[str, Any]] = [
        {
            "name": f"q{idx}",
            "type": "text",
            "label": question,
            "default": "",
        }
        for idx, question in enumerate(questions)
    ]
    return {
        "prompt": (
            "The planner needs more information before it can build a plan. "
            "Answer each question on its own line (or type 'skip' to skip)."
        ),
        "fields": fields,
    }


async def human_approval_node(state: PlanningState) -> dict[str, Any]:
    """Interrupt for human approval of the plan.

    Emits a form-schema envelope (``prompt``/``fields``/``context``) so
    any consumer — CLI, web UI, REPL — can render the pause uniformly.
    The ``context`` carries the work brief pointer and routing skeleton
    for display without an artifact read.

    Resume payload shape::

        {"action": "approve" | "revise" | "reject",
         "feedback": str | None}
    """
    pointer = state.get("work_brief_pointer")
    if not pointer:
        return {"plan_approved": False}
    decision = parse_approval_decision(
        interrupt(
            build_plan_approval_form(
                work_brief_pointer=pointer,
                routing_skeleton=state.get("routing_skeleton"),
            )
        )
    )

    if decision.action == "approve":
        return {
            "plan_approved": True,
            "messages": [{"role": "user", "content": "action=approve"}],
        }
    if (
        decision.action == "revise"
        and decision.feedback
        and check_budget(state, MAX_REVISIONS)
    ):
        return {
            "plan_approved": False,
            "human_feedback": decision.feedback,
            "messages": [
                {
                    "role": "user",
                    "content": f"action=revise, feedback={decision.feedback}",
                }
            ],
            **increment_budget(state),
        }
    msg = f"action={decision.action}" if decision.action else "action=reject"
    return {
        "plan_approved": False,
        "messages": [{"role": "user", "content": msg}],
    }


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


def _make_route_from_planner(
    max_followup_attempts: int,
) -> Callable[[PlanningState], str]:
    """Router that exits to questionnaire / approval / planning_failed."""

    def route_from_planner(state: PlanningState) -> str:
        if state.get("pending_questions") and max_followup_attempts > 0:
            return "questionnaire"
        if state.get("work_brief_pointer") is not None:
            return "human_approval"
        return "planning_failed"

    return route_from_planner


def route_from_planner(state: PlanningState) -> str:
    """Default router (``max_followup_attempts=0``) — approval or failure."""
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
    *,
    max_followup_attempts: int = 0,
) -> StateGraph[PlanningState]:
    """Build the planning subgraph with HITL approval. Returns uncompiled StateGraph.

    Args:
        hooks: Optional graph hook registry. Fires ``before_planning``
            with the planning state before the planner runs, and
            ``after_planning`` with the planner update after planning.
        max_followup_attempts: Number of questionnaire rounds allowed
            before the planner is force-planned. Default 0 means
            clarification questions are treated as planning failure
            (pipeline behaviour). Chat callers pass 1.
    """
    planner_inner = _make_planner_node(max_followup_attempts)
    route_planner = _make_route_from_planner(max_followup_attempts)

    async def _planner_with_hooks(
        state: PlanningState, config: RunnableConfig
    ) -> dict[str, Any]:
        if hooks:
            state = await hooks.run("before_planning", state)
        update = await planner_inner(state, config)
        if hooks and update.get("routing_skeleton"):
            update = cast("dict[str, Any]", await hooks.run("after_planning", update))
        return update

    planner: Any = _planner_with_hooks if hooks else planner_inner

    graph = StateGraph(PlanningState)
    graph.add_node("planner", planner)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("planning_failed", planning_failed_node)
    branches: dict[Hashable, str] = {
        "human_approval": "human_approval",
        "planning_failed": "planning_failed",
    }
    if max_followup_attempts > 0:
        graph.add_node("questionnaire", questionnaire_node)
        graph.add_edge("questionnaire", "planner")
        branches["questionnaire"] = "questionnaire"
    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", route_planner, branches)
    graph.add_conditional_edges(
        "human_approval", route_from_approval, {"planner": "planner", END: END}
    )
    graph.add_edge("planning_failed", END)
    return graph
