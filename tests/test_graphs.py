# mypy: disable-error-code="call-overload,arg-type"
"""Tests for the reference graph builders + run() sequencer.

Patches monet.agents.*._get_model so no API keys are required.
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
from monet.core.registry import default_registry  # internal: registry_scope fixture
from monet.orchestration import (
    build_execution_subgraph,
    build_planning_subgraph,
)


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
    # planner_fast wraps the model via .with_structured_output(TriageResult).
    # Route the structured chain back to the same mock so .ainvoke() still
    # returns the configured AIMessage — planner_fast falls back to
    # raw-text JSON parsing when the result isn't a TriageResult.
    mock.with_structured_output = lambda schema: mock
    return mock


# New flat-DAG brief — matches WorkBrief Pydantic schema.
_BRIEF = (
    '{"goal": "Test goal",'
    ' "nodes": ['
    '{"id": "draft", "depends_on": [],'
    ' "agent_id": "writer", "command": "deep",'
    ' "task": "write a thing"}'
    "]}"
)
# Legacy wave-schema brief — still consumed directly by execution_graph
# tests until Step 6 rewrites the execution graph.
_LEGACY_WAVE_BRIEF = (
    '{"goal": "Test goal", "phases": ['
    '{"name": "Draft", "waves": [{"items": ['
    '{"agent_id": "writer", "command": "deep", "task": "write a thing"}'
    "]}]}]}"
)
_QA = '{"verdict": "pass", "confidence": 0.9, "notes": "good"}'


async def test_planning_hitl_approve() -> None:
    with patch("monet.agents.planner._get_model", return_value=_mock(_BRIEF)):
        graph = build_planning_subgraph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "p-approve"}}
        await graph.ainvoke(
            {"task": "Write something", "revision_count": 0}, config=config
        )
        state = await graph.aget_state(config)
        assert "human_approval" in state.next

        result = await graph.ainvoke(
            Command(resume={"action": "approve"}), config=config
        )
    assert result["plan_approved"] is True
    # Pointer-only state: the full brief lives in the artifact store, only
    # pointer + routing skeleton are in LangGraph state.
    assert result["work_brief_pointer"]["key"] == "work_brief"
    assert result["routing_skeleton"]["goal"] == "Test goal"


async def test_planning_hitl_reject_then_approve() -> None:
    with patch("monet.agents.planner._get_model", return_value=_mock(_BRIEF)):
        graph = build_planning_subgraph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "p-reject"}}
        await graph.ainvoke(
            {"task": "Write something", "revision_count": 0}, config=config
        )
        # Reject with feedback → triggers replan
        await graph.ainvoke(
            Command(resume={"action": "revise", "feedback": "more depth"}),
            config=config,
        )
        state = await graph.aget_state(config)
        assert state.values["revision_count"] == 1
        assert "human_approval" in state.next
        # Approve revised plan
        result = await graph.ainvoke(
            Command(resume={"action": "approve"}), config=config
        )
    assert result["plan_approved"] is True


async def _write_brief_artifact(brief_json: str) -> dict[str, str]:
    """Write a WorkBrief JSON to the in-memory artifact store and return its pointer.

    Tests that drive the execution graph directly need a pointer in state
    even though the inject_plan_context worker hook is implemented in Step 7.
    """
    from monet import get_artifacts

    pointer = await get_artifacts().write(
        content=brief_json.encode(),
        content_type="application/json",
        summary="test brief",
        confidence=1.0,
        completeness="complete",
        key="work_brief",
    )
    return dict(pointer)


_BRIEF_SKELETON: dict[str, Any] = {
    "goal": "Test goal",
    "nodes": [
        {
            "id": "draft",
            "depends_on": [],
            "agent_id": "writer",
            "command": "deep",
        },
    ],
}


async def test_execution_graph_runs_dag() -> None:
    """Execution graph runs a simple flat DAG to completion."""
    with patch("monet.agents.writer._get_model", return_value=_mock("Some content")):
        pointer = await _write_brief_artifact(_BRIEF)
        graph = build_execution_subgraph().compile(checkpointer=MemorySaver())
        result = await graph.ainvoke(
            {
                "work_brief_pointer": pointer,
                "routing_skeleton": _BRIEF_SKELETON,
                "trace_id": "t",
                "run_id": "r",
            },
            config={"configurable": {"thread_id": "exec-1"}},  # type: ignore[arg-type]
        )
    # Node ran, marked complete, no abort.
    assert result.get("abort_reason") is None
    assert "draft" in (result.get("completed_node_ids") or [])
    assert len(result.get("wave_results") or []) == 1
    assert result["wave_results"][0]["node_id"] == "draft"
    assert result["wave_results"][0]["success"] is True


async def test_execution_graph_blocking_signal_interrupts() -> None:
    """A blocking signal from an agent triggers human_interrupt, resume retries."""
    from monet import agent as agent_decorator
    from monet.exceptions import NeedsHumanReview

    call_count = {"n": 0}

    @agent_decorator(agent_id="writer", command="deep")
    async def flaky_writer(task: str) -> str:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise NeedsHumanReview(reason="confidence too low on first pass")
        return "Successful content on retry"

    pointer = await _write_brief_artifact(_BRIEF)
    graph = build_execution_subgraph().compile(checkpointer=MemorySaver())
    config: RunnableConfig = {"configurable": {"thread_id": "exec-retry"}}

    # First pass: hits NEEDS_HUMAN_REVIEW → interrupt.
    await graph.ainvoke(
        {
            "work_brief_pointer": pointer,
            "routing_skeleton": _BRIEF_SKELETON,
            "trace_id": "t",
            "run_id": "r",
        },
        config=config,
    )
    state = await graph.aget_state(config)
    assert "human_interrupt" in state.next, (
        "Expected graph to pause at human_interrupt after blocking signal"
    )

    # Resume without abort → loops back to dispatch, retries the failed node.
    result = await graph.ainvoke(Command(resume={"action": "retry"}), config=config)
    assert call_count["n"] == 2, "Writer should have run exactly twice"
    assert result.get("abort_reason") is None
    assert "draft" in (result.get("completed_node_ids") or [])


async def test_execution_graph_aborts_on_node_failure() -> None:
    """A non-blocking node failure aborts the run."""
    from monet import agent as agent_decorator

    @agent_decorator(agent_id="writer", command="deep")
    async def boom(task: str) -> str:
        raise RuntimeError("catastrophe")

    pointer = await _write_brief_artifact(_BRIEF)
    graph = build_execution_subgraph().compile(checkpointer=MemorySaver())
    result = await graph.ainvoke(
        {
            "work_brief_pointer": pointer,
            "routing_skeleton": _BRIEF_SKELETON,
            "trace_id": "t",
            "run_id": "r",
        },
        config={"configurable": {"thread_id": "exec-abort"}},  # type: ignore[arg-type]
    )
    assert result.get("abort_reason") is not None
    assert "draft" not in (result.get("completed_node_ids") or [])


async def test_run_end_to_end() -> None:
    """Full planning → execution pipeline via direct graph invocation."""
    with (
        patch("monet.agents.planner._get_model", return_value=_mock(_BRIEF)),
        patch("monet.agents.writer._get_model", return_value=_mock("Some content")),
        patch("monet.agents.qa._get_model", return_value=_mock(_QA)),
    ):
        checkpointer = MemorySaver()
        thread_id = "e2e-test"

        # Planning with auto-approve
        planning = build_planning_subgraph().compile(checkpointer=checkpointer)
        planning_config: RunnableConfig = {
            "configurable": {"thread_id": f"{thread_id}-planning"}
        }
        await planning.ainvoke(
            {
                "task": "Write a post about AI",
                "trace_id": thread_id,
                "run_id": thread_id,
                "revision_count": 0,
            },
            config=planning_config,
        )
        planning_state = await planning.ainvoke(
            Command(resume={"action": "approve"}),
            config=planning_config,
        )
        assert planning_state.get("plan_approved") is True
        assert planning_state.get("work_brief_pointer") is not None
        assert planning_state.get("routing_skeleton") is not None

        # Execution — pointer-only, DAG traversal.
        execution = build_execution_subgraph().compile(checkpointer=checkpointer)
        exec_state = await execution.ainvoke(
            {
                "work_brief_pointer": planning_state["work_brief_pointer"],
                "routing_skeleton": planning_state["routing_skeleton"],
                "trace_id": thread_id,
                "run_id": thread_id,
            },
            config={"configurable": {"thread_id": f"{thread_id}-execution"}},
        )
    assert exec_state.get("abort_reason") is None
    assert "draft" in (exec_state.get("completed_node_ids") or [])
    assert len(exec_state["wave_results"]) == 1
