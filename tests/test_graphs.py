# mypy: disable-error-code="call-overload,arg-type"
"""Tests for the three reference graph builders + run() sequencer.

Patches monet.agents.*._get_model so no API keys are required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from monet._registry import default_registry  # internal: registry_scope fixture
from monet.catalogue import InMemoryCatalogueClient, configure_catalogue
from monet.orchestration import (
    build_entry_graph,
    build_execution_graph,
    build_planning_graph,
    run,
)


@pytest.fixture(autouse=True)
def _reset() -> Any:
    configure_catalogue(InMemoryCatalogueClient())
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
    configure_catalogue(None)


def _mock(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    return mock


_TRIAGE = (
    '{"complexity": "complex", "suggested_agents": ["writer"],'
    ' "requires_planning": true}'
)
_BRIEF = (
    '{"goal": "Test goal", "is_sensitive": false, "phases": ['
    '{"name": "Draft", "waves": [{"items": ['
    '{"agent_id": "writer", "command": "deep", "task": "write a thing"}'
    "]}]}]}"
)
_QA = '{"verdict": "pass", "confidence": 0.9, "notes": "good"}'


async def test_entry_graph_triage() -> None:
    with patch("monet.agents.planner._get_model", return_value=_mock(_TRIAGE)):
        graph = build_entry_graph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "t1"}}
        result = await graph.ainvoke(
            {"task": "Write a post", "trace_id": "t", "run_id": "r"},
            config=config,
        )
    assert result["triage"]["complexity"] == "complex"


async def test_planning_hitl_approve() -> None:
    with patch("monet.agents.planner._get_model", return_value=_mock(_BRIEF)):
        graph = build_planning_graph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "p-approve"}}
        await graph.ainvoke(
            {"task": "Write something", "revision_count": 0}, config=config
        )
        state = await graph.aget_state(config)
        assert "human_approval" in state.next

        result = await graph.ainvoke(
            Command(resume={"approved": True, "feedback": None}), config=config
        )
    assert result["plan_approved"] is True
    assert result["work_brief"]["goal"] == "Test goal"


async def test_planning_hitl_reject_then_approve() -> None:
    with patch("monet.agents.planner._get_model", return_value=_mock(_BRIEF)):
        graph = build_planning_graph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "p-reject"}}
        await graph.ainvoke(
            {"task": "Write something", "revision_count": 0}, config=config
        )
        # Reject with feedback → triggers replan
        await graph.ainvoke(
            Command(resume={"approved": False, "feedback": "more depth"}),
            config=config,
        )
        state = await graph.aget_state(config)
        assert state.values["revision_count"] == 1
        assert "human_approval" in state.next
        # Approve revised plan
        result = await graph.ainvoke(
            Command(resume={"approved": True, "feedback": None}), config=config
        )
    assert result["plan_approved"] is True


async def test_execution_graph_runs_all_waves() -> None:
    with (
        patch("monet.agents.writer._get_model", return_value=_mock("Some content")),
        patch("monet.agents.qa._get_model", return_value=_mock(_QA)),
    ):
        import json as _json

        brief = _json.loads(_BRIEF)
        graph = build_execution_graph().compile(checkpointer=MemorySaver())
        result = await graph.ainvoke(
            {
                "work_brief": brief,
                "trace_id": "t",
                "run_id": "r",
                "current_phase_index": 0,
                "current_wave_index": 0,
                "wave_results": [],
                "wave_reflections": [],
                "completed_phases": [],
                "revision_count": 0,
            },
            config={"configurable": {"thread_id": "exec-1"}},  # type: ignore[arg-type]
        )
    assert len(result["wave_results"]) == 1
    assert len(result["wave_reflections"]) == 1
    assert result["wave_reflections"][0]["verdict"] == "pass"


async def test_run_end_to_end() -> None:
    with (
        patch("monet.agents.planner._get_model") as planner_mock,
        patch("monet.agents.writer._get_model", return_value=_mock("Some content")),
        patch("monet.agents.qa._get_model", return_value=_mock(_QA)),
    ):
        # Planner mock returns triage on first call (fast), brief on second (plan)
        triage_resp = _mock(_TRIAGE).ainvoke.return_value
        brief_resp = _mock(_BRIEF).ainvoke.return_value
        planner_mock.return_value.ainvoke = AsyncMock(
            side_effect=[triage_resp, brief_resp]
        )

        result = await run("Write a post about AI", auto_approve=True)
    assert result["phase"] == "execution"
    assert len(result["execution"]["wave_results"]) == 1
