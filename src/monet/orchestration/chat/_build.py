"""Chat graph builder — composes planning + execution subgraphs with chat nodes."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from monet.orchestration.execution_graph import build_execution_subgraph
from monet.orchestration.planning_graph import build_planning_subgraph

from ._format import execution_summary_node
from ._parse import _route_after_parse, parse_command_node
from ._respond import respond_node
from ._specialist import specialist_node
from ._state import ChatState
from ._triage import _route_after_triage, triage_node

# Number of questionnaire rounds allowed before the planner is
# force-planned inside the planning subgraph. Passed into
# build_planning_subgraph so the bound lives with the graph it
# constrains.
MAX_FOLLOWUP_ATTEMPTS = 1


def _route_after_planning(state: ChatState) -> str:
    """After planning subgraph exits: execute if approved, else END."""
    if (
        state.get("plan_approved") is True
        and state.get("work_brief_pointer")
        and state.get("routing_skeleton")
    ):
        return "execution"
    return "__end__"


def build_chat_graph() -> StateGraph[ChatState]:
    """Build the chat graph. Returns uncompiled ``StateGraph[ChatState]``.

    Topology::

        START -> parse -> (triage | respond | planning | specialist)
        triage -> (respond | planning)
        planning -> (execution | END)
        execution -> execution_summary -> END
        respond -> END
        specialist -> END

    Planning and execution are mounted as compiled subgraphs — chat
    does not re-implement the planner/approval/questionnaire state
    machine. State keys flow through name-matching; ``ChatState``
    structurally inherits ``PlanningState`` and adds the
    execution-subgraph fields.
    """
    graph: StateGraph[ChatState] = StateGraph(ChatState)
    graph.add_node("parse", parse_command_node)
    graph.add_node("triage", triage_node)
    graph.add_node("respond", respond_node)
    graph.add_node("specialist", specialist_node)
    graph.add_node(
        "planning",
        build_planning_subgraph(max_followup_attempts=MAX_FOLLOWUP_ATTEMPTS).compile(),
    )
    graph.add_node("execution", build_execution_subgraph().compile())
    graph.add_node("execution_summary", execution_summary_node)

    graph.add_edge(START, "parse")
    graph.add_conditional_edges(
        "parse",
        _route_after_parse,
        {
            "triage": "triage",
            "respond": "respond",
            "planning": "planning",
            "specialist": "specialist",
        },
    )
    graph.add_conditional_edges(
        "triage",
        _route_after_triage,
        {
            "respond": "respond",
            "planning": "planning",
        },
    )
    graph.add_conditional_edges(
        "planning",
        _route_after_planning,
        {
            "execution": "execution",
            "__end__": END,
        },
    )
    graph.add_edge("execution", "execution_summary")
    graph.add_edge("execution_summary", END)
    graph.add_edge("respond", END)
    graph.add_edge("specialist", END)
    return graph
