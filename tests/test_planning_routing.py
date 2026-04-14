# mypy: disable-error-code="call-overload,arg-type,index"
"""Tests for planning graph routing and planner_node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from monet.catalogue import InMemoryCatalogueClient, configure_catalogue
from monet.core.manifest import default_manifest
from monet.core.registry import default_registry
from monet.orchestration import build_planning_graph
from monet.orchestration.planning_graph import route_from_planner
from monet.types import ArtifactPointer


@pytest.fixture
def _reset() -> Any:
    configure_catalogue(InMemoryCatalogueClient())
    with default_registry.registry_scope(), default_manifest.manifest_scope():
        import importlib

        import monet.agents.planner
        import monet.agents.writer

        for mod in (monet.agents.planner, monet.agents.writer):
            importlib.reload(mod)
        yield
    configure_catalogue(None)


def _mock(content: str) -> AsyncMock:
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    return mock


# --- Pure route_from_planner ---


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({}, "planning_failed"),
        ({"work_brief_pointer": None}, "planning_failed"),
        (
            {
                "work_brief_pointer": ArtifactPointer(
                    artifact_id="a", url="u", key="work_brief"
                )
            },
            "human_approval",
        ),
    ],
)
def test_route_from_planner(state: dict[str, Any], expected: str) -> None:
    assert route_from_planner(state) == expected  # type: ignore[arg-type]


# --- planner_node integration ---


_VALID_BRIEF = (
    '{"goal": "Test goal",'
    ' "nodes": ['
    '{"id": "draft", "depends_on": [],'
    ' "agent_id": "writer", "command": "deep",'
    ' "task": "write a thing"}'
    "],"
    ' "is_sensitive": false}'
)


async def test_planner_node_success_stores_pointer_and_skeleton(_reset: Any) -> None:
    with patch("monet.agents.planner._get_model", return_value=_mock(_VALID_BRIEF)):
        graph = build_planning_graph().compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "p-ok"}}
        await graph.ainvoke(
            {"task": "Write something", "revision_count": 0},
            config=config,  # type: ignore[arg-type]
        )
        state = await graph.aget_state(config)  # type: ignore[arg-type]
    # Should pause at human_approval with pointer + skeleton set.
    assert "human_approval" in state.next
    assert state.values.get("work_brief_pointer") is not None
    assert state.values.get("work_brief_pointer")["key"] == "work_brief"
    skeleton = state.values.get("routing_skeleton")
    assert skeleton is not None
    assert skeleton["goal"] == "Test goal"
    assert len(skeleton["nodes"]) == 1


async def test_planner_node_failure_routes_to_planning_failed(_reset: Any) -> None:
    # Invalid JSON will cause the planner to raise → result.success False.
    with patch("monet.agents.planner._get_model", return_value=_mock("not json")):
        graph = build_planning_graph().compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "p-fail"}}
        result = await graph.ainvoke(
            {"task": "Write something", "revision_count": 0},
            config=config,  # type: ignore[arg-type]
        )
    # Graph should reach terminal planning_failed (END), no interrupt.
    assert result.get("plan_approved") is False
    assert result.get("planner_error") is not None
    assert result.get("work_brief_pointer") is None
    assert result.get("routing_skeleton") is None


async def test_planner_node_invalid_dag_routes_to_failed(_reset: Any) -> None:
    # Valid JSON but violates DAG rules — dangling dep.
    bad = (
        '{"goal": "Bad DAG",'
        ' "nodes": ['
        '{"id": "a", "depends_on": ["missing"],'
        ' "agent_id": "writer", "command": "deep", "task": "x"}'
        "],"
        ' "is_sensitive": false}'
    )
    with patch("monet.agents.planner._get_model", return_value=_mock(bad)):
        graph = build_planning_graph().compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "p-bad-dag"}}
        result = await graph.ainvoke(
            {"task": "x", "revision_count": 0},
            config=config,  # type: ignore[arg-type]
        )
    assert result.get("plan_approved") is False
    assert result.get("planner_error") is not None


# --- OTel span on failure ---


async def test_planning_failed_emits_otel_span(_reset: Any) -> None:
    """planning_failed_node must emit an OTel span for observability."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    # Set up a fresh provider so we can capture spans.
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Swap the tracer used by the module.
    from monet.orchestration import planning_graph as pg

    original_tracer = pg._tracer
    pg._tracer = provider.get_tracer("monet.orchestration.planning")
    try:
        with patch("monet.agents.planner._get_model", return_value=_mock("bad")):
            graph = build_planning_graph().compile(checkpointer=MemorySaver())
            config = {"configurable": {"thread_id": "p-otel"}}
            await graph.ainvoke(
                {"task": "x", "revision_count": 0},
                config=config,  # type: ignore[arg-type]
            )
        spans = exporter.get_finished_spans()
        assert any(s.name == "planning.failed" for s in spans), (
            f"Expected planning.failed span; got {[s.name for s in spans]}"
        )
    finally:
        pg._tracer = original_tracer
        # Avoid mutating the global trace provider further — new provider is
        # local to this test and not registered globally.
        _ = trace
