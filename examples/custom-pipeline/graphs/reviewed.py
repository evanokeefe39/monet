"""Graph-level extension — compound pipeline + a user-owned review node.

Composes the three built-in subgraphs (``entry`` / ``planning`` /
``execution``) under a parent ``StateGraph[MyRunState]`` with an extra
``review`` node after execution. Demonstrates monet's OCP extension
pattern:

- Inherit ``RunState`` to add user fields; LangGraph preserves
  user-only keys through subgraph boundaries.
- Reuse ``build_entry_subgraph`` / ``build_planning_subgraph`` /
  ``build_execution_subgraph`` as compiled nodes — no fork of monet.
- Add your node anywhere in the graph; use user-defined reducers for
  append-style fields.
"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph import END, START, StateGraph

from monet.orchestration import (
    RunState,
    build_entry_subgraph,
    build_execution_subgraph,
    build_planning_subgraph,
)


def _append_str(existing: list[str], new: list[str]) -> list[str]:
    return existing + new


class MyRunState(RunState, total=False):
    """Extended run state with user-only fields.

    ``review_score`` and ``review_notes`` are invisible to monet's
    subgraphs — they pass through each subgraph node untouched.
    """

    review_score: float | None
    review_notes: Annotated[list[str], _append_str]


async def review_node(state: MyRunState) -> dict[str, Any]:
    """Score the execution output based on a trivial heuristic.

    Real review nodes would run a QA agent, score the artifacts, or
    gate a follow-up loop. This node just computes a score from the
    number of successful wave results so the example stays runnable
    without additional LLM calls beyond the built-in pipeline.
    """
    results = state.get("wave_results") or []
    successful = [r for r in results if isinstance(r, dict) and r.get("success")]
    score = len(successful) / len(results) if results else 0.0
    return {
        "review_score": score,
        "review_notes": [f"reviewed {len(results)} wave_results; {len(successful)} ok"],
    }


def build_reviewed_graph() -> StateGraph[MyRunState]:
    """Build the user-extended compound graph.

    Idiomatic LangGraph: subgraphs compile to nodes under a parent
    with a different state schema. Shared keys flow by name; user-only
    keys pass through subgraph boundaries untouched.
    """
    g: StateGraph[MyRunState] = StateGraph(MyRunState)
    g.add_node("entry", build_entry_subgraph().compile())
    g.add_node("planning", build_planning_subgraph().compile())
    g.add_node("execution", build_execution_subgraph().compile())
    g.add_node("review", review_node)

    g.add_edge(START, "entry")
    g.add_conditional_edges(
        "entry",
        _route_after_entry,
        {"planning": "planning", END: END},
    )
    g.add_conditional_edges(
        "planning",
        _route_after_planning,
        {"execution": "execution", END: END},
    )
    g.add_edge("execution", "review")
    g.add_edge("review", END)
    return g


def _route_after_entry(state: MyRunState) -> str:
    triage = state.get("triage") or {}
    if triage.get("complexity") == "simple":
        return END
    return "planning"


def _route_after_planning(state: MyRunState) -> str:
    if (
        state.get("plan_approved")
        and state.get("routing_skeleton")
        and state.get("work_brief_pointer")
    ):
        return "execution"
    return END
