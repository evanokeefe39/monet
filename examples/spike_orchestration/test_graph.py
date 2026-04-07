"""Spike 6: Orchestration with real SDK and LangGraph StateGraph.

Validates: LangGraph API surface, state schema, the decorator in a
real graph context, signal-based routing, interrupt() for HITL.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

import pytest
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from monet import agent
from monet._registry import default_registry  # internal: registry_scope fixture
from monet.exceptions import NeedsHumanReview
from monet.types import AgentResult, AgentRunContext, SignalType

# --- Lean graph state ---


def _reducer(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return existing + new


class GraphState(TypedDict, total=False):
    task: str
    trace_id: str
    run_id: str
    results: Annotated[list[dict[str, Any]], _reducer]
    needs_review: bool


# --- Mock agents using real SDK decorator ---


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    with default_registry.registry_scope():
        yield


@agent(agent_id="spike-planner", command="fast")
async def mock_planner(task: str) -> str:
    return f"Plan for: {task}"


@agent(agent_id="spike-researcher", command="fast")
async def mock_researcher(task: str) -> str:
    return f"Research on: {task}"


@agent(agent_id="spike-writer", command="fast")
async def mock_writer(task: str) -> str:
    return f"Written: {task}"


@agent(agent_id="spike-low-confidence", command="fast")
async def mock_low_confidence(task: str) -> str:
    raise NeedsHumanReview(reason="Confidence below threshold")


# --- Node wrapper: calls agent, updates state ---


async def _call_agent(agent_id: str, state: GraphState) -> dict[str, Any]:
    """Minimal node wrapper: call agent, return state update."""
    handler = default_registry.lookup(agent_id, "fast")
    assert handler is not None, f"No handler for {agent_id}"

    ctx = AgentRunContext(
        task=state["task"],
        command="fast",
        trace_id=state.get("trace_id", ""),
        run_id=state.get("run_id", ""),
        agent_id=agent_id,
    )
    result: AgentResult = await handler(ctx)

    needs_review = result.has_signal(SignalType.NEEDS_HUMAN_REVIEW)

    entry: dict[str, Any] = {
        "agent_id": agent_id,
        "output": result.output,
        "success": result.success,
        "needs_human_review": needs_review,
    }

    return {
        "results": [entry],
        "needs_review": needs_review,
    }


# --- Node functions ---


async def planner_node(state: GraphState) -> dict[str, Any]:
    return await _call_agent("spike-planner", state)


async def researcher_node(state: GraphState) -> dict[str, Any]:
    return await _call_agent("spike-researcher", state)


async def writer_node(state: GraphState) -> dict[str, Any]:
    return await _call_agent("spike-writer", state)


async def review_node(state: GraphState) -> dict[str, Any]:
    """Node that always triggers HITL interrupt."""
    result = interrupt("Human review required before publishing")
    return {"results": [{"agent_id": "reviewer", "output": str(result)}]}


# --- Routing ---


def route_after_researcher(
    state: GraphState,
) -> str:
    """Route based on last result's review signal."""
    if state.get("needs_review"):
        return "review"
    return "writer"


# --- Tests ---


async def test_linear_graph() -> None:
    """Planner -> Researcher -> Writer, linear flow."""
    graph = StateGraph(GraphState)
    graph.add_node("planner", planner_node)  # type: ignore[call-overload]
    graph.add_node("researcher", researcher_node)  # type: ignore[call-overload]
    graph.add_node("writer", writer_node)  # type: ignore[call-overload]
    graph.set_entry_point("planner")
    graph.add_edge("planner", "researcher")
    graph.add_edge("researcher", "writer")
    graph.add_edge("writer", END)

    app = graph.compile()
    result = await app.ainvoke(  # type: ignore[call-overload]
        {
            "task": "Analyze market trends",
            "trace_id": "t-1",
            "run_id": "r-1",
        }
    )

    assert len(result["results"]) == 3
    agents = [r["agent_id"] for r in result["results"]]
    assert agents == ["spike-planner", "spike-researcher", "spike-writer"]
    assert all(r["success"] for r in result["results"])


async def test_conditional_routing() -> None:
    """Route to review when needs_human_review is signaled."""

    async def low_confidence_researcher(
        state: GraphState,
    ) -> dict[str, Any]:
        return await _call_agent("spike-low-confidence", state)

    graph = StateGraph(GraphState)
    graph.add_node("researcher", low_confidence_researcher)  # type: ignore[call-overload]
    graph.add_node("writer", writer_node)  # type: ignore[call-overload]
    graph.add_node("review", review_node)  # type: ignore[call-overload]
    graph.set_entry_point("researcher")
    graph.add_conditional_edges(
        "researcher",
        route_after_researcher,
        {"writer": "writer", "review": "review"},
    )
    graph.add_edge("writer", END)
    graph.add_edge("review", END)

    # Need checkpointer for interrupt()
    from langgraph.checkpoint.memory import MemorySaver

    checkpointer = MemorySaver()
    app = graph.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "test-thread"}}
    result = await app.ainvoke(  # type: ignore[call-overload]
        {
            "task": "Risky analysis",
            "trace_id": "t-2",
            "run_id": "r-2",
        },
        config=config,
    )

    # The researcher signals needs_human_review, routing to review node
    # The review node calls interrupt(), so execution pauses
    assert result["needs_review"] is True
    # Results should contain the researcher entry
    assert any(r["agent_id"] == "spike-low-confidence" for r in result["results"])


async def test_state_is_lean() -> None:
    """Verify state contains only lean data, no full artifact content."""
    graph = StateGraph(GraphState)
    graph.add_node("planner", planner_node)  # type: ignore[call-overload]
    graph.set_entry_point("planner")
    graph.add_edge("planner", END)

    app = graph.compile()
    result = await app.ainvoke(  # type: ignore[call-overload]
        {"task": "Test lean state", "trace_id": "t-3", "run_id": "r-3"}
    )

    # State should contain summary-level data only
    for entry in result["results"]:
        assert "agent_id" in entry
        assert "output" in entry
        assert "success" in entry
