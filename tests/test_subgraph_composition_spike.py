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


# ── Interrupt + resume across subgraph boundary ────────────────────


class _InterruptSubState(TypedDict, total=False):
    task: str
    approved: bool | None


def _build_interrupt_sub() -> StateGraph[_InterruptSubState]:
    from langgraph.types import interrupt

    async def _pause(state: _InterruptSubState) -> dict[str, Any]:
        decision = interrupt({"prompt": "approve?"})
        return {"approved": bool(decision.get("approved"))}

    g: StateGraph[_InterruptSubState] = StateGraph(_InterruptSubState)
    g.add_node("pause", _pause)
    g.add_edge(START, "pause")
    g.add_edge("pause", END)
    return g


class _InterruptParentState(TypedDict, total=False):
    task: str
    approved: bool | None
    finalised: bool | None


async def _finalise(state: _InterruptParentState) -> dict[str, Any]:
    return {"finalised": state.get("approved") is True}


async def test_interrupt_inside_subgraph_pauses_parent_and_resumes() -> None:
    """Subgraph's interrupt() pauses the parent; Command(resume=...) continues
    the subgraph from inside, then the parent proceeds to the next node.

    This is the load-bearing property for Track B.3 — without it, the
    compound-graph plan cannot use LangGraph's native HITL through a
    subgraph node.
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    parent: StateGraph[_InterruptParentState] = StateGraph(_InterruptParentState)
    parent.add_node("phase", _build_interrupt_sub().compile())
    parent.add_node("finalise", _finalise)
    parent.add_edge(START, "phase")
    parent.add_edge("phase", "finalise")
    parent.add_edge("finalise", END)
    graph = parent.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "ir-1"}}

    # First invocation runs the subgraph until interrupt() fires.
    await graph.ainvoke({"task": "hi"}, config=config)  # type: ignore[call-overload]
    state = await graph.aget_state(config)  # type: ignore[arg-type]
    assert state.next, "expected parent paused mid-run"
    # `finalise` must not have run yet — parent is still paused inside `phase`.
    assert not state.values.get("finalised")

    # Resume with approval. The subgraph's interrupt resolves and returns
    # {"approved": True}; the parent proceeds to the finalise node.
    result = await graph.ainvoke(  # type: ignore[call-overload]
        Command(resume={"approved": True}),
        config=config,
    )
    assert result["approved"] is True
    assert result["finalised"] is True


# ── Streaming through subgraph ─────────────────────────────────────


class _StreamSubState(TypedDict, total=False):
    task: str
    output: str | None


def _build_stream_sub() -> StateGraph[_StreamSubState]:
    async def _work(state: _StreamSubState) -> dict[str, Any]:
        return {"output": f"done:{state.get('task')}"}

    g: StateGraph[_StreamSubState] = StateGraph(_StreamSubState)
    g.add_node("work", _work)
    g.add_edge(START, "work")
    g.add_edge("work", END)
    return g


async def test_updates_stream_surfaces_subgraph_node_events() -> None:
    """Updates-mode streaming at the parent must report the subgraph node's
    state writes, so a client can observe progress happening *inside* a
    compiled subgraph. Otherwise the collapse hides execution events.
    """

    class _StreamParentState(TypedDict, total=False):
        task: str
        output: str | None
        finalised: bool | None

    async def _finalise_s(state: _StreamParentState) -> dict[str, Any]:
        return {"finalised": True}

    parent: StateGraph[_StreamParentState] = StateGraph(_StreamParentState)
    parent.add_node("phase", _build_stream_sub().compile())
    parent.add_node("after", _finalise_s)
    parent.add_edge(START, "phase")
    parent.add_edge("phase", "after")
    parent.add_edge("after", END)
    graph = parent.compile()

    updates: list[Any] = []
    async for chunk in graph.astream(  # type: ignore[call-overload]
        {"task": "hello"},
        stream_mode="updates",
        subgraphs=True,
    ):
        updates.append(chunk)

    # Expect at least: phase node update (from subgraph) + after node update.
    # The subgraph node update carries namespace info when subgraphs=True,
    # so updates is a list of (namespace, {node_name: patch}) tuples.
    # Flatten and verify both the subgraph's "work" write and parent
    # "after" write are visible.
    seen_writes: set[str] = set()
    for item in updates:
        if isinstance(item, tuple):
            _ns, payload = item
        else:
            payload = item
        if isinstance(payload, dict):
            for node_name, patch in payload.items():
                if isinstance(patch, dict):
                    seen_writes.update(patch.keys())
                    seen_writes.add(f"node:{node_name}")

    # The subgraph's "work" node wrote "output"; the parent's "after"
    # wrote "finalised". Both must be visible in the parent stream.
    assert "output" in seen_writes, (
        f"subgraph node's state write lost — saw {seen_writes}"
    )
    assert "finalised" in seen_writes, (
        f"parent node's state write lost — saw {seen_writes}"
    )
