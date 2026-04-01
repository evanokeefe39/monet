"""Tests for the orchestration layer."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, StateGraph

from monet._decorator import agent
from monet._registry import default_registry
from monet._types import AgentRunContext
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
    """Review signal triggers interrupt — must run in graph context."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END

    node = create_node("orch-review-needed")
    graph = StateGraph(GraphState)
    graph.add_node("review", node)  # type: ignore[call-overload]
    graph.set_entry_point("review")
    graph.add_edge("review", END)

    checkpointer = MemorySaver()
    app = graph.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "review-test"}}
    result = await app.ainvoke(  # type: ignore[call-overload]
        {"task": "Risky", "trace_id": "t-2", "run_id": "r-2"},
        config=config,
    )
    # interrupt() pauses the graph — check __interrupt__ data
    assert "__interrupt__" in result
    assert len(result["__interrupt__"]) == 1
    interrupt_data = result["__interrupt__"][0].value
    assert interrupt_data["agent_id"] == "orch-review-needed"
    assert interrupt_data["reason"] == "Low confidence"


async def test_create_node_missing_handler() -> None:
    node = create_node("nonexistent-agent")
    state: GraphState = {"task": "x"}
    with pytest.raises(LookupError, match="No handler"):
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


# --- invoke_agent transport ---


async def test_invoke_agent_local() -> None:
    from monet.orchestration._invoke import invoke_agent

    ctx = AgentRunContext(task="Test invoke", agent_id="orch-planner", command="fast")
    result = await invoke_agent("orch-planner", "fast", ctx)
    assert result.success is True
    assert isinstance(result.output, str)
    assert "Test invoke" in result.output


async def test_invoke_agent_missing() -> None:
    from monet.orchestration._invoke import invoke_agent

    ctx = AgentRunContext(task="x", agent_id="ghost", command="fast")
    with pytest.raises(LookupError, match="No handler"):
        await invoke_agent("ghost", "fast", ctx)


# --- HITL interrupt ---


async def test_node_interrupt_on_review() -> None:
    """Node wrapper calls interrupt() when needs_human_review is True."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    node = create_node("orch-review-needed", interrupt_on_review=True)
    graph = StateGraph(GraphState)
    graph.add_node("reviewer", node)  # type: ignore[call-overload]
    graph.set_entry_point("reviewer")
    graph.add_edge("reviewer", END)

    checkpointer = MemorySaver()
    app = graph.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "hitl-test"}}
    result = await app.ainvoke(  # type: ignore[call-overload]
        {"task": "Needs review", "trace_id": "t-hitl", "run_id": "r-hitl"},
        config=config,
    )

    # interrupt() pauses the graph — check __interrupt__ data
    assert "__interrupt__" in result
    interrupt_data = result["__interrupt__"][0].value
    assert interrupt_data["agent_id"] == "orch-review-needed"


async def test_node_no_interrupt_when_disabled() -> None:
    """interrupt_on_review=False skips interrupt even with review signal."""
    node = create_node("orch-review-needed", interrupt_on_review=False)
    graph = StateGraph(GraphState)
    graph.add_node("reviewer", node)  # type: ignore[call-overload]
    graph.set_entry_point("reviewer")
    graph.add_edge("reviewer", END)

    app = graph.compile()
    result = await app.ainvoke(  # type: ignore[call-overload]
        {"task": "No interrupt", "trace_id": "t-ni", "run_id": "r-ni"}
    )
    assert result["needs_review"] is True
    assert len(result["results"]) == 1


# --- RetryPolicy from descriptors ---


def test_build_retry_policy() -> None:
    from monet.descriptors import CommandDescriptor, RetryConfig
    from monet.orchestration._retry import build_retry_policy

    cmd = CommandDescriptor(
        retry=RetryConfig(
            max_retries=5,
            retryable_errors=["unexpected_error"],
            backoff_factor=2.0,
        )
    )
    policy = build_retry_policy(cmd)
    assert policy.max_attempts == 6  # retries + 1
    assert policy.backoff_factor == 2.0


def test_build_retry_policy_defaults() -> None:
    from monet.descriptors import CommandDescriptor
    from monet.orchestration._retry import build_retry_policy

    cmd = CommandDescriptor()
    policy = build_retry_policy(cmd)
    assert policy.max_attempts == 4  # default 3 retries + 1
    assert policy.backoff_factor == 1.0
