"""Bespoke chat graph — zero reuse of monet.orchestration.chat.

Topology::

    START -> route
          -> respond -> END            (conversational turn)
          -> plan -> plan_approval
                       -> risk_review
                         -> execute -> END   (approved + risk accepted)
                         -> END              (risk blocked)
                     -> END                  (rejected)

Demonstrates that the TUI / MonetClient only require two things from a
chat graph:

1. ``state["messages"]`` is an append-only list of
   ``{role, content}`` dicts.
2. Interrupts emit a ``{prompt, fields}`` form-schema envelope; the
   resume payload comes back keyed by the declared field names.

Everything else — node names, routing, interrupt shapes, agent IDs,
state keys — is user-owned. This file ships two deliberately distinct
interrupt envelopes (``plan_approval`` and ``risk_review``) to prove
the TUI renders any compliant envelope without special-casing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from monet.orchestration import invoke_agent

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


def _append_messages(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return (existing or []) + new


class MycoChatState(TypedDict, total=False):
    messages: Annotated[list[dict[str, Any]], _append_messages]
    user_input: str
    route: str
    plan_artifact_id: str
    plan_summary: str
    plan_decision: str
    risk_decision: str
    research_output: str
    draft_output: str


def _last_user_content(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


async def route_node(state: MycoChatState) -> dict[str, Any]:
    """Slash-free routing: ``/plan ...`` triggers planning, else respond."""
    content = _last_user_content(state.get("messages") or [])
    stripped = content.strip()
    if stripped.lower().startswith("/plan"):
        task = stripped[len("/plan") :].strip() or "unspecified goal"
        return {"route": "plan", "user_input": task}
    return {"route": "respond", "user_input": stripped}


def _route_after_route(state: MycoChatState) -> str:
    return state.get("route") or "respond"


async def respond_node(state: MycoChatState) -> dict[str, Any]:
    """Delegate conversational replies to the bespoke conversationalist."""
    task = state.get("user_input") or _last_user_content(state.get("messages") or [])
    result = await invoke_agent("myco_conversationalist", command="reply", task=task)
    content = (
        result.output if isinstance(result.output, str) else str(result.output or "")
    )
    return {
        "messages": [{"role": "assistant", "content": content}],
    }


async def plan_node(state: MycoChatState) -> dict[str, Any]:
    """Call the bespoke planner and stash the artifact pointer."""
    task = state.get("user_input") or ""
    result = await invoke_agent("myco_planner", command="plan", task=task)
    output = result.output if isinstance(result.output, dict) else {}
    summary = str(output.get("summary") or "")
    plan_artifact_id = str(output.get("plan_id") or "")
    return {
        "plan_artifact_id": plan_artifact_id,
        "plan_summary": summary,
        "messages": [{"role": "assistant", "content": f"Draft plan ready:\n{summary}"}],
    }


async def plan_approval_node(state: MycoChatState) -> dict[str, Any]:
    """Interrupt with a bespoke ``plan_approval`` form envelope.

    Field vocabulary is intentionally small: a radio for the decision
    plus a feedback textarea. The resume payload is
    ``{"decision": "accept"|"reject", "feedback": str|None}``.
    """
    decision = interrupt(
        {
            "kind": "plan_approval",
            "prompt": (
                "Review the draft plan below and approve or reject.\n"
                f"{state.get('plan_summary') or ''}"
            ),
            "fields": [
                {
                    "name": "decision",
                    "type": "radio",
                    "label": "Decision",
                    "options": [
                        {"value": "accept", "label": "Accept plan"},
                        {"value": "reject", "label": "Reject plan"},
                    ],
                },
                {
                    "name": "feedback",
                    "type": "textarea",
                    "label": "Feedback (optional)",
                    "required": False,
                },
            ],
        }
    )
    chosen = "reject"
    if isinstance(decision, dict):
        raw = decision.get("decision")
        if raw in ("accept", "reject"):
            chosen = str(raw)
    if chosen == "reject":
        return {
            "plan_decision": "reject",
            "messages": [
                {"role": "assistant", "content": "Plan rejected — no action taken."}
            ],
        }
    return {"plan_decision": "accept"}


async def risk_review_node(state: MycoChatState) -> dict[str, Any]:
    """Interrupt with a deliberately different envelope (``risk_review``).

    Resume payload: ``{"tolerance": "accept"|"mitigate"|"block"}``.
    Distinct from plan approval — proves the TUI does not special-case
    on form kind.
    """
    tolerance = interrupt(
        {
            "kind": "risk_review",
            "prompt": ("Before executing, pick a risk posture for this plan."),
            "fields": [
                {
                    "name": "tolerance",
                    "type": "radio",
                    "label": "Risk posture",
                    "options": [
                        {"value": "accept", "label": "Accept risk"},
                        {"value": "mitigate", "label": "Mitigate then execute"},
                        {"value": "block", "label": "Block — do not execute"},
                    ],
                }
            ],
        }
    )
    chosen = "block"
    if isinstance(tolerance, dict):
        raw = tolerance.get("tolerance")
        if raw in ("accept", "mitigate", "block"):
            chosen = str(raw)
    if chosen == "block":
        return {
            "risk_decision": "block",
            "messages": [
                {
                    "role": "assistant",
                    "content": "Execution blocked by risk review.",
                }
            ],
        }
    return {"risk_decision": chosen}


async def execute_node(state: MycoChatState) -> dict[str, Any]:
    """Run the two-step execution: researcher then writer."""
    task = state.get("user_input") or ""
    research = await invoke_agent("myco_researcher", command="gather", task=task)
    research_out = (
        research.output
        if isinstance(research.output, str)
        else str(research.output or "")
    )
    draft = await invoke_agent(
        "myco_writer",
        command="compose",
        task=f"{task}\n\nFindings:\n{research_out}",
    )
    draft_out = (
        draft.output if isinstance(draft.output, str) else str(draft.output or "")
    )
    return {
        "research_output": research_out,
        "draft_output": draft_out,
        "messages": [
            {
                "role": "assistant",
                "content": (
                    f"Executed (risk posture: {state.get('risk_decision')}):\n\n"
                    f"{draft_out}"
                ),
            }
        ],
    }


def _route_after_plan_approval(state: MycoChatState) -> str:
    return "risk_review" if state.get("plan_decision") == "accept" else END


def _route_after_risk_review(state: MycoChatState) -> str:
    return "execute" if state.get("risk_decision") in ("accept", "mitigate") else END


def build_chat_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    """Compile the bespoke chat graph."""
    graph: StateGraph[MycoChatState] = StateGraph(MycoChatState)
    graph.add_node("route", route_node)
    graph.add_node("respond", respond_node)
    graph.add_node("plan", plan_node)
    graph.add_node("plan_approval", plan_approval_node)
    graph.add_node("risk_review", risk_review_node)
    graph.add_node("execute", execute_node)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        _route_after_route,
        {"respond": "respond", "plan": "plan"},
    )
    graph.add_edge("respond", END)
    graph.add_edge("plan", "plan_approval")
    graph.add_conditional_edges(
        "plan_approval",
        _route_after_plan_approval,
        {"risk_review": "risk_review", END: END},
    )
    graph.add_conditional_edges(
        "risk_review",
        _route_after_risk_review,
        {"execute": "execute", END: END},
    )
    graph.add_edge("execute", END)
    return graph.compile()
