"""Default-pipeline adapter: event projection + pointer-shape regressions.

Pins the ``work_brief_pointer`` + ``routing_skeleton`` state shape so
the quickstart-empty-plan regression can't return silently, plus the
wave-batching / PlanInterrupt / RunFailed projections owned by the
default pipeline adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from monet.client import MonetClient, RunFailed
from monet.client._run_state import _RunStore
from monet.pipelines.default import (
    PlanInterrupt,
    PlanReady,
    WaveComplete,
)
from monet.pipelines.default import (
    run as run_default,
)
from monet.pipelines.default._inputs import execution_input
from tests._fakes import _FakeLangGraphClient

if TYPE_CHECKING:
    from monet.types import ArtifactPointer


# ── execution_input shape ───────────────────────────────────────────


def test_execution_input_carries_pointer_and_skeleton() -> None:
    pointer: ArtifactPointer = {
        "artifact_id": "brief-1",
        "url": "mem://brief-1",
        "key": "work_brief",
    }
    skeleton = {
        "goal": "Summarize X",
        "nodes": [
            {"id": "n1", "agent_id": "researcher", "command": "fast", "depends_on": []},
        ],
    }

    out = execution_input(pointer, skeleton, run_id="r-1")

    assert out["work_brief_pointer"] == pointer
    assert out["routing_skeleton"] == skeleton
    assert out["completed_node_ids"] == []
    assert out["wave_results"] == []
    assert out["wave_reflections"] == []
    assert out["run_id"] == "r-1"
    assert out["trace_id"] == "trace-r-1"
    # Legacy fields MUST NOT reappear — every one of them broke quickstart.
    assert "work_brief" not in out
    assert "current_phase_index" not in out
    assert "current_wave_index" not in out
    assert "completed_phases" not in out


# ── Default pipeline adapter: event projection ──────────────────────


def _bare_client(states: list[dict[str, Any]]) -> MonetClient:
    """Build a MonetClient without touching the real network."""
    client = MonetClient.__new__(MonetClient)
    client._client = _FakeLangGraphClient(states)  # type: ignore[assignment]
    client._store = _RunStore()
    client._graph_roles = {"chat": "chat", "entry": "entry"}
    client._chat_graph_id = "chat"
    client._entrypoints = {"default": {"graph": "entry"}}
    return client


async def test_adapter_emits_plan_ready_with_nodes() -> None:
    """Auto-approve path: PlanReady carries the skeleton's goal and nodes."""
    skeleton = {
        "goal": "Draft a brief on AI in healthcare",
        "nodes": [
            {"id": "r1", "agent_id": "researcher", "command": "fast", "depends_on": []},
            {
                "id": "w1",
                "agent_id": "writer",
                "command": "fast",
                "depends_on": ["r1"],
            },
        ],
    }
    pointer = {"artifact_id": "brief-1", "url": "mem://brief-1", "key": "work_brief"}

    # One state per thread created: entry → planning → execution.
    states: list[dict[str, Any]] = [
        {"triage": {"complexity": "bounded", "suggested_agents": ["researcher"]}},
        {
            "plan_approved": True,
            "work_brief_pointer": pointer,
            "routing_skeleton": skeleton,
        },
        {
            "wave_results": [
                {
                    "node_id": "r1",
                    "agent_id": "researcher",
                    "command": "fast",
                    "success": True,
                    "output": "ok",
                    "signals": [],
                    "artifacts": [],
                },
                {
                    "node_id": "w1",
                    "agent_id": "writer",
                    "command": "fast",
                    "success": True,
                    "output": "ok",
                    "signals": [],
                    "artifacts": [],
                },
            ],
            "wave_reflections": [],
            "completed_node_ids": ["r1", "w1"],
            "routing_skeleton": skeleton,
        },
    ]

    client = _bare_client(states)
    events = [e async for e in run_default(client, "topic", auto_approve=True)]

    plan_ready = next(e for e in events if isinstance(e, PlanReady))
    assert plan_ready.goal == "Draft a brief on AI in healthcare"
    assert len(plan_ready.nodes) == 2
    assert plan_ready.nodes[0]["id"] == "r1"
    assert plan_ready.nodes[1]["depends_on"] == ["r1"]

    # WaveComplete splits on the r1 → w1 dependency edge.
    waves = [e for e in events if isinstance(e, WaveComplete)]
    assert len(waves) == 2
    assert waves[0].node_ids == ["r1"]
    assert waves[1].node_ids == ["w1"]


async def test_adapter_emits_plan_interrupt_when_awaiting_approval() -> None:
    """No auto-approve + next=human_approval: yield PlanInterrupt."""
    skeleton = {
        "goal": "Do the thing",
        "nodes": [
            {"id": "a", "agent_id": "researcher", "command": "fast", "depends_on": []},
        ],
    }
    pointer = {"artifact_id": "b", "url": "mem://b", "key": "work_brief"}
    states: list[dict[str, Any]] = [
        {"triage": {"complexity": "bounded", "suggested_agents": []}},
        {"work_brief_pointer": pointer, "routing_skeleton": skeleton},
    ]

    client = _bare_client(states)
    client._client.threads.set_next([[], ["human_approval"]])  # type: ignore[attr-defined]

    events = [e async for e in run_default(client, "topic", auto_approve=False)]
    interrupt = next(e for e in events if isinstance(e, PlanInterrupt))
    assert interrupt.work_brief_pointer == pointer
    assert interrupt.routing_skeleton == skeleton


async def test_adapter_fails_when_planner_omits_skeleton() -> None:
    """plan_approved=True but missing skeleton/pointer must RunFail."""
    states: list[dict[str, Any]] = [
        {"triage": {"complexity": "bounded", "suggested_agents": []}},
        {"plan_approved": True, "planner_error": None},
    ]

    client = _bare_client(states)
    events = [e async for e in run_default(client, "topic", auto_approve=True)]
    failed = next((e for e in events if isinstance(e, RunFailed)), None)
    assert failed is not None
    assert "work_brief_pointer" in failed.error or "routing_skeleton" in failed.error


# ── Event dataclass shape regression ───────────────────────────────


def test_plan_ready_has_nodes_not_phases() -> None:
    evt = PlanReady(run_id="r", goal="g", nodes=[{"id": "a"}])
    assert evt.goal == "g"
    assert evt.nodes == [{"id": "a"}]
    with pytest.raises(TypeError):
        PlanReady(run_id="r", goal="g", phases=[])  # type: ignore[call-arg]
