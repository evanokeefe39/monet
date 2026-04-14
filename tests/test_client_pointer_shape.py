"""Tests for the post-refactor client state shape.

After commit a176030 the planning/execution state uses
``work_brief_pointer`` + ``routing_skeleton`` instead of the legacy
inline ``work_brief`` dict. These tests pin the client's wire helpers
and ``MonetClient.run()`` / ``get_run()`` to the new shape so the
quickstart-empty-plan regression can't return silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from monet.client import MonetClient
from monet.client._events import (
    PlanInterrupt,
    PlanReady,
    RunDetail,
    WaveComplete,
)
from monet.client._wire import execution_input

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


# ── MonetClient.run() yields PlanReady with nodes ───────────────────


@dataclass
class _FakeThread:
    values: dict[str, Any]
    next_nodes: list[str] = field(default_factory=list)


@dataclass
class _FakeChunk:
    event: str = "updates"
    data: Any = None


class _FakeThreads:
    def __init__(self, states: list[dict[str, Any]]) -> None:
        # `states` is a per-drain sequence: state after entry, planning, execution.
        self._states = list(states)
        self._next_nodes: list[list[str]] = [[] for _ in states]
        self._created_threads: list[str] = []
        # map thread_id -> current state index
        self._state_idx: dict[str, int] = {}
        self._counter = 0

    def set_next(self, per_drain: list[list[str]]) -> None:
        self._next_nodes = per_drain

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        self._counter += 1
        tid = f"t-{self._counter}"
        idx = self._counter - 1
        self._state_idx[tid] = idx
        meta = kwargs.get("metadata") or {}
        self._created_threads.append(tid)
        return {"thread_id": tid, "metadata": meta}

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        idx = self._state_idx.get(thread_id, 0)
        return {
            "values": self._states[idx],
            "next": self._next_nodes[idx] if idx < len(self._next_nodes) else [],
        }

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class _FakeRuns:
    def stream(
        self,
        thread_id: str,
        graph_id: str,
        **kwargs: Any,
    ) -> Any:
        """Return an async iterator that yields nothing.

        ``langgraph_sdk.RunsClient.stream`` returns an async iterator directly
        (not a coroutine), so the fake must too. The client reads final thread
        state via ``get_state`` after the stream completes.
        """

        async def _gen() -> Any:
            if False:
                yield _FakeChunk()  # pragma: no cover
            return

        return _gen()


class _FakeLangGraphClient:
    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.threads = _FakeThreads(states)
        self.runs = _FakeRuns()


async def test_run_emits_plan_ready_with_nodes() -> None:
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

    # One state per thread created, in order:
    #   1. entry (triage)  2. planning  3. execution
    states = [
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

    client = MonetClient.__new__(MonetClient)
    client._client = _FakeLangGraphClient(states)  # type: ignore[assignment,arg-type]
    from monet._graph_config import DEFAULT_GRAPH_ROLES
    from monet.client._run_state import _RunStore

    client._graph_ids = DEFAULT_GRAPH_ROLES.copy()  # type: ignore[attr-defined]
    client._store = _RunStore()  # type: ignore[attr-defined]

    events = [e async for e in client.run("topic", auto_approve=True)]
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


async def test_run_emits_plan_interrupt_when_awaiting_approval() -> None:
    """No auto-approve + next=human_approval: yield PlanInterrupt(goal, nodes)."""
    skeleton = {
        "goal": "Do the thing",
        "nodes": [
            {"id": "a", "agent_id": "researcher", "command": "fast", "depends_on": []},
        ],
    }
    pointer = {"artifact_id": "b", "url": "mem://b", "key": "work_brief"}
    states = [
        {"triage": {"complexity": "bounded", "suggested_agents": []}},
        {"work_brief_pointer": pointer, "routing_skeleton": skeleton},
    ]

    client = MonetClient.__new__(MonetClient)
    client._client = _FakeLangGraphClient(states)  # type: ignore[assignment,arg-type]
    client._client.threads.set_next([[], ["human_approval"]])  # type: ignore[union-attr,attr-defined]
    from monet._graph_config import DEFAULT_GRAPH_ROLES
    from monet.client._run_state import _RunStore

    client._graph_ids = DEFAULT_GRAPH_ROLES.copy()  # type: ignore[attr-defined]
    client._store = _RunStore()  # type: ignore[attr-defined]

    events = [e async for e in client.run("topic", auto_approve=False)]
    interrupt = next(e for e in events if isinstance(e, PlanInterrupt))
    assert interrupt.goal == "Do the thing"
    assert interrupt.nodes == skeleton["nodes"]


async def test_run_fails_when_planner_omits_skeleton() -> None:
    """plan_approved=True but missing skeleton/pointer must RunFail, not silently OK."""
    from monet.client._events import RunFailed

    states = [
        {"triage": {"complexity": "bounded", "suggested_agents": []}},
        {"plan_approved": True, "planner_error": None},
    ]

    client = MonetClient.__new__(MonetClient)
    client._client = _FakeLangGraphClient(states)  # type: ignore[assignment,arg-type]
    from monet._graph_config import DEFAULT_GRAPH_ROLES
    from monet.client._run_state import _RunStore

    client._graph_ids = DEFAULT_GRAPH_ROLES.copy()  # type: ignore[attr-defined]
    client._store = _RunStore()  # type: ignore[attr-defined]

    events = [e async for e in client.run("topic", auto_approve=True)]
    failed = next((e for e in events if isinstance(e, RunFailed)), None)
    assert failed is not None, "Missing pointer/skeleton must produce RunFailed"
    assert "work_brief_pointer" in failed.error or "routing_skeleton" in failed.error


# ── get_run() surfaces routing_skeleton in RunDetail ───────────────


async def test_get_run_populates_routing_skeleton() -> None:
    skeleton = {
        "goal": "G",
        "nodes": [{"id": "n", "agent_id": "a", "command": "c", "depends_on": []}],
    }
    pointer = {"artifact_id": "x", "url": "mem://x", "key": "work_brief"}

    class _FakeThreadsForRun:
        def __init__(self) -> None:
            self._counter = 0

        async def search(
            self,
            *,
            metadata: dict[str, Any] | None = None,
            limit: int = 1,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            graph = (metadata or {}).get("monet_graph")
            if graph == "entry":
                return [{"thread_id": "e1"}]
            if graph == "planning":
                return [{"thread_id": "p1"}]
            if graph == "execution":
                return [{"thread_id": "x1"}]
            return []

        async def get_state(self, thread_id: str) -> dict[str, Any]:
            if thread_id == "e1":
                return {"values": {"triage": {"complexity": "bounded"}}, "next": []}
            if thread_id == "p1":
                return {
                    "values": {
                        "work_brief_pointer": pointer,
                        "routing_skeleton": skeleton,
                    },
                    "next": [],
                }
            return {
                "values": {
                    "wave_results": [{"node_id": "n", "success": True}],
                    "wave_reflections": [],
                },
                "next": [],
            }

    class _FakeLGC:
        def __init__(self) -> None:
            self.threads = _FakeThreadsForRun()

    client = MonetClient.__new__(MonetClient)
    client._client = _FakeLGC()  # type: ignore[assignment]
    from monet._graph_config import DEFAULT_GRAPH_ROLES
    from monet.client._run_state import _RunStore

    client._graph_ids = DEFAULT_GRAPH_ROLES.copy()  # type: ignore[attr-defined]
    client._store = _RunStore()  # type: ignore[attr-defined]

    detail = await client.get_run("r-1")
    assert isinstance(detail, RunDetail)
    assert detail.routing_skeleton == skeleton
    assert detail.work_brief_pointer == pointer
    # Legacy field removed
    assert not hasattr(detail, "work_brief")


# ── Event dataclass shape ───────────────────────────────────────────


def test_plan_ready_has_nodes_not_phases() -> None:
    evt = PlanReady(run_id="r", goal="g", nodes=[{"id": "a"}])
    assert evt.goal == "g"
    assert evt.nodes == [{"id": "a"}]
    with pytest.raises(TypeError):
        # Passing legacy field must fail — old consumers will loudly break.
        PlanReady(run_id="r", goal="g", phases=[])  # type: ignore[call-arg]
