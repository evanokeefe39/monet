"""Spike: verify LangGraph subgraph-as-node composition under a parent
with a different state schema.

This is the foundational check for Track B (the subgraph-as-node
collapse). Three properties must hold before we proceed:

1. A compiled subgraph with its own TypedDict state can be added as
   a node under a parent ``StateGraph[RunState]``. Shared keys flow
   through by name.
2. Fields that exist on the parent but NOT on the subgraph state
   survive the subgraph call untouched — this is the OCP guarantee
   user extension depends on.
3. A user's ``MyRunState(RunState, total=False)`` with extra keys
   plus a user-owned node around the built-in subgraph preserves
   the user's fields and allows reading/writing them alongside
   monet's fields.

If any of these fail, the Track B design needs revisiting before
B.2/B.3 land.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

import pytest

pytest.importorskip("langgraph")

from langgraph.graph import END, START, StateGraph


def _append(existing: list[str], new: list[str]) -> list[str]:
    return existing + new


class _ParentState(TypedDict, total=False):
    task: str
    triage: dict[str, Any] | None
    user_only_field: str
    user_notes: Annotated[list[str], _append]


class _SubState(TypedDict, total=False):
    task: str
    triage: dict[str, Any] | None


def _triage_node(state: _SubState) -> dict[str, Any]:
    return {"triage": {"complexity": "simple", "task": state.get("task")}}


def _build_sub() -> StateGraph[_SubState]:
    g: StateGraph[_SubState] = StateGraph(_SubState)
    g.add_node("triage", _triage_node)
    g.add_edge(START, "triage")
    g.add_edge("triage", END)
    return g


async def test_subgraph_sees_shared_keys() -> None:
    """Subgraph reads ``task`` from parent and writes ``triage`` back."""
    parent: StateGraph[_ParentState] = StateGraph(_ParentState)
    parent.add_node("phase", _build_sub().compile())
    parent.add_edge(START, "phase")
    parent.add_edge("phase", END)
    graph = parent.compile()

    result = await graph.ainvoke({"task": "hello"})  # type: ignore[call-overload]
    assert result["task"] == "hello"
    assert result["triage"] == {"complexity": "simple", "task": "hello"}


async def test_parent_only_fields_survive_subgraph_call() -> None:
    """Fields only on the parent must pass through a subgraph node untouched."""
    parent: StateGraph[_ParentState] = StateGraph(_ParentState)
    parent.add_node("phase", _build_sub().compile())
    parent.add_edge(START, "phase")
    parent.add_edge("phase", END)
    graph = parent.compile()

    result = await graph.ainvoke(  # type: ignore[call-overload]
        {"task": "hi", "user_only_field": "custom-value"},
    )
    # Core OCP property: subgraph didn't mention user_only_field;
    # it must still be in the result unchanged.
    assert result["user_only_field"] == "custom-value"


async def test_user_extension_adds_node_and_keys() -> None:
    """User extends parent state + adds a custom node around the subgraph."""

    class _UserState(_ParentState, total=False):
        review_score: float | None

    async def _review(state: _UserState) -> dict[str, Any]:
        return {
            "review_score": 0.9,
            "user_notes": [f"reviewed {state.get('task')}"],
        }

    parent: StateGraph[_UserState] = StateGraph(_UserState)
    parent.add_node("phase", _build_sub().compile())
    parent.add_node("review", _review)
    parent.add_edge(START, "phase")
    parent.add_edge("phase", "review")
    parent.add_edge("review", END)
    graph = parent.compile()

    result = await graph.ainvoke({"task": "build"})  # type: ignore[call-overload]
    assert result["triage"]["complexity"] == "simple"
    assert result["review_score"] == 0.9
    assert result["user_notes"] == ["reviewed build"]


async def test_runstate_accepts_user_extension_at_type_level() -> None:
    """monet's public RunState supports the MyRunState(RunState) pattern."""
    from monet.orchestration import RunState

    class _MyRunState(RunState, total=False):  # type: ignore[misc]
        custom_field: str

    state: _MyRunState = {"task": "x", "custom_field": "y"}
    assert state["task"] == "x"
    assert state["custom_field"] == "y"
