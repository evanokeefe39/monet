# mypy: disable-error-code="call-overload,arg-type"
"""Tests for the compound default graph (entry → planning → execution).

Validates the Track B.3 collapse: one StateGraph[RunState], one thread,
one checkpointer, LangGraph-native interrupt. Patches
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
from monet.core.manifest import default_manifest
from monet.core.registry import default_registry
from monet.orchestration import build_default_graph


@pytest.fixture(autouse=True)
def _reset() -> Any:
    configure_artifacts(InMemoryArtifactClient())
    with default_registry.registry_scope(), default_manifest.manifest_scope():
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
    return mock


_TRIAGE_COMPLEX = (
    '{"complexity": "complex", "suggested_agents": ["writer"],'
    ' "requires_planning": true}'
)
_TRIAGE_SIMPLE = (
    '{"complexity": "simple", "suggested_agents": [], "requires_planning": false}'
)
_BRIEF = (
    '{"goal": "Test goal",'
    ' "nodes": ['
    '{"id": "draft", "depends_on": [],'
    ' "agent_id": "writer", "command": "deep",'
    ' "task": "write a thing"}'
    "]}"
)
_QA = '{"verdict": "pass", "confidence": 0.9, "notes": "good"}'


async def test_compound_graph_short_circuits_on_simple_triage() -> None:
    """triage.complexity=simple → entry subgraph ends the whole pipeline."""
    with patch("monet.agents.planner._get_model", return_value=_mock(_TRIAGE_SIMPLE)):
        graph = build_default_graph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "simple-1"}}
        result = await graph.ainvoke(
            {"task": "trivial", "trace_id": "t", "run_id": "r"},
            config=config,
        )
    assert result["triage"]["complexity"] == "simple"
    # Planning and execution never ran; their fields stay absent.
    assert not result.get("work_brief_pointer")
    assert not result.get("wave_results")


async def test_compound_graph_pauses_at_planning_interrupt() -> None:
    """Planning subgraph's interrupt pauses the parent thread."""
    with (
        patch("monet.agents.planner._get_model") as planner_mock,
    ):
        triage = _mock(_TRIAGE_COMPLEX).ainvoke.return_value
        brief = _mock(_BRIEF).ainvoke.return_value
        planner_mock.return_value.ainvoke = AsyncMock(side_effect=[triage, brief])

        graph = build_default_graph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "pause-1"}}
        await graph.ainvoke(
            {"task": "real", "trace_id": "t", "run_id": "r", "revision_count": 0},
            config=config,
        )
        state = await graph.aget_state(config)
        # Paused: .next reports the PARENT node name (per Track B spike).
        assert list(state.next) == ["planning"]
        # During a subgraph pause, planning's writes haven't merged back to
        # the parent yet — the skeleton/pointer live inside the interrupt
        # payload, which UIs read for rendering.
        assert state.tasks, "expected paused task"
        interrupt_value = state.tasks[0].interrupts[0].value
        # Form-schema envelope: skeleton lives in context.
        assert interrupt_value["fields"][0]["name"] == "action"
        assert interrupt_value["context"]["routing_skeleton"]["goal"] == "Test goal"


async def test_compound_graph_approves_and_drives_execution() -> None:
    """Full pipeline via one thread: pause → resume → execution → END."""
    with (
        patch("monet.agents.planner._get_model") as planner_mock,
        patch("monet.agents.writer._get_model", return_value=_mock("Some content")),
        patch("monet.agents.qa._get_model", return_value=_mock(_QA)),
    ):
        triage = _mock(_TRIAGE_COMPLEX).ainvoke.return_value
        brief = _mock(_BRIEF).ainvoke.return_value
        planner_mock.return_value.ainvoke = AsyncMock(side_effect=[triage, brief])

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
    with (
        patch("monet.agents.planner._get_model") as planner_mock,
    ):
        triage = _mock(_TRIAGE_COMPLEX).ainvoke.return_value
        brief = _mock(_BRIEF).ainvoke.return_value
        planner_mock.return_value.ainvoke = AsyncMock(side_effect=[triage, brief])

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
