# mypy: disable-error-code="call-overload,arg-type"
"""Drive ``build_execution_subgraph`` directly with a WorkBrief pointer.

Covers the invocable-execution path that the scheduler will use: a
caller supplies ``{work_brief_pointer, routing_skeleton}`` (plus run and
trace IDs) and execution runs to END without any planning step.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("langgraph")


from monet import agent, get_artifacts
from monet.artifacts import InMemoryArtifactClient, configure_artifacts
from monet.core.registry import default_registry
from monet.orchestration import build_execution_subgraph
from monet.orchestration._state import WorkBrief, WorkBriefNode


async def test_execution_runs_frozen_brief_without_planning() -> None:
    """Compile the subgraph, ainvoke with a pointer, confirm nodes ran."""
    configure_artifacts(InMemoryArtifactClient())
    try:
        with default_registry.registry_scope():

            @agent(agent_id="stub_runner", command="go", pool="local")
            async def runner() -> dict[str, Any]:
                return {"ran": True}

            brief = WorkBrief(
                goal="frozen brief",
                nodes=[
                    WorkBriefNode(
                        id="only",
                        depends_on=[],
                        agent_id="stub_runner",
                        command="go",
                        task="do",
                    )
                ],
            )
            pointer = await get_artifacts().write(
                json.dumps(brief.model_dump()).encode(),
                content_type="application/json",
                summary="frozen brief",
                confidence=1.0,
                completeness="complete",
                tags={"work_brief": True},
            )
            pointer["key"] = "work_brief"
            skeleton = brief.to_routing_skeleton().model_dump()

            compiled = build_execution_subgraph().compile()
            state = await compiled.ainvoke(
                {
                    "work_brief_pointer": dict(pointer),
                    "routing_skeleton": skeleton,
                    "run_id": "r1",
                    "trace_id": "t1",
                }
            )
            assert state.get("abort_reason") is None
            assert state["completed_node_ids"] == ["only"]
            assert any(r["node_id"] == "only" for r in state["wave_results"])
    finally:
        configure_artifacts(None)


async def test_execution_aborts_on_missing_pointer() -> None:
    """Missing ``work_brief_pointer`` aborts cleanly in ``initialise_execution``."""
    compiled = build_execution_subgraph().compile()
    state = await compiled.ainvoke(
        {
            "routing_skeleton": {
                "goal": "x",
                "nodes": [
                    {
                        "id": "n",
                        "depends_on": [],
                        "agent_id": "a",
                        "command": "c",
                    }
                ],
            },
            "run_id": "r",
            "trace_id": "t",
        }
    )
    assert state.get("abort_reason", "").startswith("No work_brief_pointer")
