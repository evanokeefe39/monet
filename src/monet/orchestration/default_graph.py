"""Compound default graph — entry → planning → execution.

Composes the three pipeline subgraphs as nodes under a single parent
``StateGraph[RunState]`` with one thread, one checkpointer, and
LangGraph's native ``interrupt()`` / ``Command(resume=...)`` for HITL.
Replaces the prior three-thread composition.

Extension pattern for self-hosting users::

    from monet.orchestration import (
        RunState,
        build_entry_subgraph,
        build_planning_subgraph,
        build_execution_subgraph,
    )

    class MyRunState(RunState, total=False):
        review_score: float | None

    def build_reviewed_default() -> StateGraph[MyRunState]:
        g = StateGraph(MyRunState)
        g.add_node("entry", build_entry_subgraph().compile())
        g.add_node("planning", build_planning_subgraph().compile())
        g.add_node("execution", build_execution_subgraph().compile())
        g.add_node("review", my_review_node)
        ...
        return g

Register the extended graph in ``aegra.json`` and declare it as an
``[entrypoints.<name>]`` in ``monet.toml`` to drive it from
``monet run``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from ._state import RunState
from .entry_graph import build_entry_subgraph
from .execution_graph import build_execution_subgraph
from .planning_graph import build_planning_subgraph

if TYPE_CHECKING:
    from monet.core.hooks import GraphHookRegistry


def _route_after_entry(state: RunState) -> str:
    """Short-circuit when triage classifies the task as simple."""
    triage = state.get("triage") or {}
    if triage.get("complexity") == "simple":
        return END
    return "planning"


def _route_after_planning(state: RunState) -> str:
    """Proceed to execution iff the plan was approved and a skeleton exists."""
    if (
        state.get("plan_approved")
        and state.get("routing_skeleton")
        and state.get("work_brief_pointer")
    ):
        return "execution"
    return END


def build_default_graph(
    hooks: GraphHookRegistry | None = None,
) -> StateGraph[RunState]:
    """Compose entry/planning/execution as subgraphs under one RunState graph.

    The returned graph is uncompiled; Aegra / LangGraph Server compiles
    it and attaches its own checkpointer. One thread owns the whole
    run; HITL interrupts in planning or execution pause the parent
    thread and resume via ``Command(resume=...)`` dispatched by the
    client.

    Args:
        hooks: Optional graph hook registry forwarded to each subgraph.

    Returns:
        An uncompiled ``StateGraph[RunState]``.
    """
    graph: StateGraph[RunState] = StateGraph(RunState)
    graph.add_node("entry", build_entry_subgraph(hooks).compile())
    graph.add_node("planning", build_planning_subgraph(hooks).compile())
    graph.add_node("execution", build_execution_subgraph(hooks).compile())

    graph.add_edge(START, "entry")
    graph.add_conditional_edges(
        "entry",
        _route_after_entry,
        {"planning": "planning", END: END},
    )
    graph.add_conditional_edges(
        "planning",
        _route_after_planning,
        {"execution": "execution", END: END},
    )
    graph.add_edge("execution", END)
    return graph
