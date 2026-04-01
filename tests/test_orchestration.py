"""Tests for the orchestration layer."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, StateGraph

from monet._decorator import agent
from monet._registry import default_registry
from monet.catalogue._memory import InMemoryCatalogueClient
from monet.exceptions import NeedsHumanReview
from monet.orchestration._content_limit import enforce_content_limit
from monet.orchestration._node_wrapper import create_node
from monet.orchestration._state import GraphState


@pytest.fixture(autouse=True)
def _clean_registry() -> None:  # type: ignore[misc]
    with default_registry.registry_scope():
        yield


# --- Mock agents ---


@agent(agent_id="orch-planner")
async def mock_planner(task: str) -> str:
    return f"Plan: {task}"


@agent(agent_id="orch-writer")
async def mock_writer(task: str) -> str:
    return f"Written: {task}"


@agent(agent_id="orch-review-needed")
async def mock_review_agent(task: str) -> str:
    raise NeedsHumanReview(reason="Low confidence")


# --- Content limit tests ---


def test_content_limit_within_limit() -> None:
    entry: dict[str, Any] = {"output": "short", "agent_id": "test"}
    result = enforce_content_limit(entry, limit=100)
    assert result["output"] == "short"


def test_content_limit_exceeds_truncates() -> None:
    long_output = "x" * 5000
    entry: dict[str, Any] = {"output": long_output, "agent_id": "test"}
    result = enforce_content_limit(entry, limit=100)
    assert len(result["output"]) == 100
    assert "summary" in result


def test_content_limit_with_catalogue() -> None:
    catalogue = InMemoryCatalogueClient()
    long_output = "y" * 5000
    entry: dict[str, Any] = {
        "output": long_output,
        "agent_id": "test",
        "confidence": 0.9,
    }
    result = enforce_content_limit(entry, limit=100, catalogue=catalogue)
    assert len(result["output"]) == 100
    assert "artifact_url" in result
    assert result["artifact_url"].startswith("memory://")


# --- Node wrapper tests ---


async def test_create_node_basic() -> None:
    node = create_node("orch-planner")
    state: GraphState = {
        "task": "Test planning",
        "trace_id": "t-1",
        "run_id": "r-1",
    }
    result = await node(state)
    assert len(result["results"]) == 1
    assert result["results"][0]["agent_id"] == "orch-planner"
    assert result["results"][0]["success"] is True
    assert "Test planning" in result["results"][0]["output"]


async def test_create_node_signals_review() -> None:
    node = create_node("orch-review-needed")
    state: GraphState = {"task": "Risky", "trace_id": "t-2", "run_id": "r-2"}
    result = await node(state)
    assert result["needs_review"] is True
    assert result["results"][0]["needs_human_review"] is True


async def test_create_node_missing_handler() -> None:
    node = create_node("nonexistent-agent")
    state: GraphState = {"task": "x"}
    with pytest.raises(LookupError, match="No handler registered"):
        await node(state)


# --- Full graph integration ---


async def test_graph_with_create_node() -> None:
    graph = StateGraph(GraphState)
    graph.add_node("planner", create_node("orch-planner"))  # type: ignore[call-overload]
    graph.add_node("writer", create_node("orch-writer"))  # type: ignore[call-overload]
    graph.set_entry_point("planner")
    graph.add_edge("planner", "writer")
    graph.add_edge("writer", END)

    app = graph.compile()
    result = await app.ainvoke(  # type: ignore[call-overload]
        {"task": "Full graph test", "trace_id": "t-3", "run_id": "r-3"}
    )

    assert len(result["results"]) == 2
    agents = [r["agent_id"] for r in result["results"]]
    assert agents == ["orch-planner", "orch-writer"]
    assert all(r["success"] for r in result["results"])
