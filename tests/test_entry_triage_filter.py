# mypy: disable-error-code="call-overload,arg-type"
"""Triage filters unknown agents from suggested_agents (I2)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

from monet.artifacts import InMemoryArtifactClient, configure_artifacts
from monet.core.manifest import default_manifest
from monet.core.registry import default_registry
from monet.orchestration import build_entry_subgraph


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
    mock.with_structured_output = lambda schema: mock
    return mock


async def test_triage_filters_unknown_agent(caplog: pytest.LogCaptureFixture) -> None:
    triage_json = (
        '{"complexity": "complex",'
        ' "suggested_agents": ["writer", "bogus_agent"],'
        ' "requires_planning": true}'
    )
    with patch("monet.agents.planner._get_model", return_value=_mock(triage_json)):
        graph = build_entry_subgraph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "t1"}}
        with caplog.at_level(logging.WARNING, logger="monet.orchestration.entry_graph"):
            result = await graph.ainvoke(
                {"task": "write a thing", "trace_id": "t", "run_id": "r"},
                config=config,
            )
    assert result["triage"]["suggested_agents"] == ["writer"]
    assert any("bogus_agent" in rec.message for rec in caplog.records)


async def test_triage_keeps_all_known_agents() -> None:
    triage_json = (
        '{"complexity": "complex",'
        ' "suggested_agents": ["writer", "researcher", "qa"],'
        ' "requires_planning": true}'
    )
    with patch("monet.agents.planner._get_model", return_value=_mock(triage_json)):
        graph = build_entry_subgraph().compile(checkpointer=MemorySaver())
        config: RunnableConfig = {"configurable": {"thread_id": "t2"}}
        result = await graph.ainvoke(
            {"task": "write a thing", "trace_id": "t", "run_id": "r"},
            config=config,
        )
    assert set(result["triage"]["suggested_agents"]) == {"writer", "researcher", "qa"}
