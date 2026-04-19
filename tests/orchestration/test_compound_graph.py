# mypy: disable-error-code="call-overload,arg-type"
"""Tests for the compound default graph (planning → execution).

Validates the collapse: one StateGraph[RunState], one thread, one
checkpointer, LangGraph-native interrupt. Patches
``monet.agents.*._get_model`` so no API keys are required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

from monet.artifacts import InMemoryArtifactClient, configure_artifacts
from monet.core.registry import default_registry
from monet.orchestration import build_default_graph


@pytest.fixture(autouse=True)
def _reset() -> Any:
    configure_artifacts(InMemoryArtifactClient())
    with default_registry.registry_scope():
        import importlib

        import monet.agents.planner
        import monet.agents.publisher
        import monet.agents.qa
        import monet.agents.researcher
        import monet.agents.writer

        for mod in (
            monet.agents.planner,
            monet.agents.researcher,
            monet.agents.writer,
            monet.agents.qa,
            monet.agents.publisher,
        ):
            importlib.reload(mod)
        yield
    configure_artifacts(None)


def _mock(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    # planner wraps the model via .with_structured_output(...); route the
    # structured chain back to the same mock so ainvoke() serves the same
    # content (the planner falls back to raw-text parse when the result
    # isn't a structured instance).
    mock.with_structured_output = lambda schema: mock
    return mock


_BRIEF = (
    '{"goal": "Test goal",'
    ' "nodes": ['
    '{"id": "draft", "depends_on": [],'
    ' "agent_id": "writer", "command": "deep",'
    ' "task": "write a thing"}'
    "]}"
)
_QA = '{"verdict": "pass", "confidence": 0.9, "notes": "good"}'


async def test_compound_graph_pauses_at_planning_interrupt() -> None:
    """Planning subgraph's interrupt pauses the parent thread."""
    with patch("monet.agents.planner._get_model", return_value=_mock(_BRIEF)):
        graph = build_default_graph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "pause-1"}}
        await graph.ainvoke(
            {"task": "real", "trace_id": "t", "run_id": "r", "revision_count": 0},
            config=config,
        )
        state = await graph.aget_state(config)
        # Paused: .next reports the PARENT node name.
        assert list(state.next) == ["planning"]
        assert state.tasks, "expected paused task"
        interrupt_value = state.tasks[0].interrupts[0].value
        # Form-schema envelope: skeleton lives in context.
        assert interrupt_value["fields"][0]["name"] == "action"
        assert interrupt_value["context"]["routing_skeleton"]["goal"] == "Test goal"


async def test_compound_graph_approves_and_drives_execution() -> None:
    """Full pipeline via one thread: pause → resume → execution → END."""
    with (
        patch("monet.agents.planner._get_model", return_value=_mock(_BRIEF)),
        patch("monet.agents.writer._get_model", return_value=_mock("Some content")),
        patch("monet.agents.qa._get_model", return_value=_mock(_QA)),
    ):
        graph = build_default_graph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "e2e-1"}}

        # Run until the planning interrupt pauses the thread.
        await graph.ainvoke(
            {"task": "drive", "trace_id": "t", "run_id": "r", "revision_count": 0},
            config=config,
        )
        state = await graph.aget_state(config)
        assert list(state.next) == ["planning"]

        # Resume with approval — execution subgraph drives the DAG.
        result = await graph.ainvoke(
            Command(resume={"action": "approve"}),
            config=config,
        )

    assert result.get("plan_approved") is True
    assert result.get("abort_reason") is None
    assert result.get("wave_results"), "expected execution to produce wave_results"
    assert result["wave_results"][0]["node_id"] == "draft"


async def test_compound_graph_ends_on_plan_rejection() -> None:
    """Plan rejected → plan_approved=False → pipeline ends without execution."""
    with patch("monet.agents.planner._get_model", return_value=_mock(_BRIEF)):
        graph = build_default_graph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "reject-1"}}

        await graph.ainvoke(
            {"task": "reject me", "trace_id": "t", "run_id": "r", "revision_count": 0},
            config=config,
        )
        result = await graph.ainvoke(
            Command(resume={"action": "reject"}),
            config=config,
        )

    assert result.get("plan_approved") is False
    # Execution was skipped: no wave_results.
    assert not result.get("wave_results")


async def test_compound_graph_default_nodes_are_planning_and_execution() -> None:
    """Pipeline topology: no entry node, just planning + execution."""
    graph = build_default_graph()
    nodes = set(graph.nodes.keys())
    assert nodes == {"planning", "execution"}
