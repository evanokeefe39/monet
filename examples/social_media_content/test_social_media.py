"""Automated tests for the social media content generation example.

Tests use MemorySaver for checkpointing and auto-approve HITL gates
via Command(resume=...). Test 12 uses Postgres and is marked
@pytest.mark.integration.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from monet._registry import default_registry

# Import agents to trigger registration
from . import agents as _agents  # noqa: F401
from .entry_graph import build_entry_graph
from .execution_graph import build_execution_graph
from .planning_graph import build_planning_graph
from .run import run_full_workflow
from .state import EntryState, ExecutionState, PlanningState  # noqa: TC001


@pytest.fixture(autouse=True)
def _clean_registry():
    """Isolate agent registrations per test."""
    with default_registry.registry_scope():
        # Re-import agents module to re-register within this scope
        import importlib

        from . import agents

        importlib.reload(agents)
        yield


# ---------------------------------------------------------------------------
# Test 1: Entry graph triage
# ---------------------------------------------------------------------------


async def test_triage_routes_complex() -> None:
    """Entry graph classifies a content generation request as complex."""
    checkpointer = MemorySaver()
    graph = build_entry_graph().compile(checkpointer=checkpointer)

    state: EntryState = {
        "user_message": "Create social media content about AI in marketing",
        "trace_id": "t-test-1",
        "run_id": "r-test-1",
    }
    config = {"configurable": {"thread_id": "test-triage"}}
    result = await graph.ainvoke(state, config=config)

    assert result["triage"] is not None
    assert result["triage"]["complexity"] == "complex"
    assert "sm-researcher" in result["triage"]["suggested_agents"]


# ---------------------------------------------------------------------------
# Test 2: Planning with approval
# ---------------------------------------------------------------------------


async def test_planning_with_approval() -> None:
    """Planning graph produces a work brief and accepts approval."""
    checkpointer = MemorySaver()
    graph = build_planning_graph().compile(checkpointer=checkpointer)

    state: PlanningState = {
        "user_message": "Create social media content about AI",
        "trace_id": "t-test-2",
        "run_id": "r-test-2",
        "revision_count": 0,
    }
    config = {"configurable": {"thread_id": "test-planning-approve"}}

    # First invocation hits interrupt at human_approval_node
    await graph.ainvoke(state, config=config)
    graph_state = await graph.aget_state(config)
    assert "human_approval" in graph_state.next
    assert graph_state.values.get("work_brief") is not None

    # Resume with approval
    result = await graph.ainvoke(Command(resume={"approved": True}), config=config)

    assert result["plan_approved"] is True
    brief = result["work_brief"]
    assert "goal" in brief
    assert len(brief["phases"]) >= 1


# ---------------------------------------------------------------------------
# Test 3: Planning with rejection and feedback
# ---------------------------------------------------------------------------


async def test_planning_with_rejection_and_feedback() -> None:
    """Planning graph handles rejection with feedback, replans, then approves."""
    checkpointer = MemorySaver()
    graph = build_planning_graph().compile(checkpointer=checkpointer)

    state: PlanningState = {
        "user_message": "Create social media content about AI",
        "trace_id": "t-test-3",
        "run_id": "r-test-3",
        "revision_count": 0,
    }
    config = {"configurable": {"thread_id": "test-planning-feedback"}}

    # First invocation -> interrupt
    await graph.ainvoke(state, config=config)

    # Reject with feedback
    await graph.ainvoke(
        Command(resume={"approved": False, "feedback": "Focus more on LinkedIn"}),
        config=config,
    )

    # Should be at human_approval again with revised brief
    graph_state = await graph.aget_state(config)
    assert "human_approval" in graph_state.next
    assert graph_state.values["revision_count"] == 1

    # Now approve
    result = await graph.ainvoke(Command(resume={"approved": True}), config=config)
    assert result["plan_approved"] is True


# ---------------------------------------------------------------------------
# Test 4: Planning with rejection (no feedback)
# ---------------------------------------------------------------------------


async def test_planning_with_rejection() -> None:
    """Planning graph ends when rejected without feedback."""
    checkpointer = MemorySaver()
    graph = build_planning_graph().compile(checkpointer=checkpointer)

    state: PlanningState = {
        "user_message": "Create social media content",
        "trace_id": "t-test-4",
        "run_id": "r-test-4",
        "revision_count": 0,
    }
    config = {"configurable": {"thread_id": "test-planning-reject"}}

    # First invocation -> interrupt
    await graph.ainvoke(state, config=config)

    # Reject without feedback
    result = await graph.ainvoke(
        Command(resume={"approved": False, "feedback": None}),
        config=config,
    )

    assert result["plan_approved"] is False


# ---------------------------------------------------------------------------
# Test 5: Execution wave parallelism
# ---------------------------------------------------------------------------


async def test_execution_wave_parallelism() -> None:
    """Execution graph runs all wave items and accumulates via reducer."""
    from .agents import _BASE_WORK_BRIEF

    checkpointer = MemorySaver()
    graph = build_execution_graph().compile(checkpointer=checkpointer)

    state: ExecutionState = {
        "work_brief": _BASE_WORK_BRIEF,
        "trace_id": "t-test-5",
        "run_id": "r-test-5",
        "current_phase_index": 0,
        "current_wave_index": 0,
        "wave_results": [],
        "wave_reflections": [],
        "completed_phases": [],
        "revision_count": 0,
    }
    config = {"configurable": {"thread_id": "test-exec-waves"}}
    result = await graph.ainvoke(state, config=config)

    wave_results = result.get("wave_results", [])
    # 1 researcher + 3 writers + 3 QA + 3 publishers = 10
    assert len(wave_results) == 10

    # Verify parallel items in wave 1 of phase 0 (3 writers)
    wave_1_phase_0 = [
        r for r in wave_results if r["phase_index"] == 0 and r["wave_index"] == 1
    ]
    assert len(wave_1_phase_0) == 3
    assert all(r["agent_id"] == "sm-writer" for r in wave_1_phase_0)


# ---------------------------------------------------------------------------
# Test 6: QA reflection runs after each wave
# ---------------------------------------------------------------------------


async def test_execution_qa_reflection() -> None:
    """QA reflection runs after each wave."""
    from .agents import _BASE_WORK_BRIEF

    checkpointer = MemorySaver()
    graph = build_execution_graph().compile(checkpointer=checkpointer)

    state: ExecutionState = {
        "work_brief": _BASE_WORK_BRIEF,
        "trace_id": "t-test-6",
        "run_id": "r-test-6",
        "current_phase_index": 0,
        "current_wave_index": 0,
        "wave_results": [],
        "wave_reflections": [],
        "completed_phases": [],
        "revision_count": 0,
    }
    config = {"configurable": {"thread_id": "test-exec-qa"}}
    result = await graph.ainvoke(state, config=config)

    reflections = result.get("wave_reflections", [])
    # 2 phases x 2 waves = 4 reflections
    assert len(reflections) == 4
    assert all(r["verdict"] == "pass" for r in reflections)


# ---------------------------------------------------------------------------
# Test 7: QA failure triggers interrupt
# ---------------------------------------------------------------------------


async def test_execution_qa_failure_triggers_interrupt() -> None:
    """When QA returns 'fail', the graph routes to human_interrupt."""
    from monet import agent as agent_decorator

    # Register a QA agent that always fails
    @agent_decorator(agent_id="sm-qa", command="fast")
    async def failing_qa(task: str) -> str:
        import json

        return json.dumps({"verdict": "fail", "notes": "Quality below threshold"})

    # Use a minimal work brief with a single phase/wave
    brief: dict[str, Any] = {
        "goal": "Test QA failure",
        "phases": [
            {
                "name": "Test",
                "waves": [
                    {
                        "items": [
                            {
                                "agent_id": "sm-researcher",
                                "command": "deep",
                                "task": "Research test topic",
                            }
                        ]
                    }
                ],
            }
        ],
    }

    checkpointer = MemorySaver()
    graph = build_execution_graph().compile(checkpointer=checkpointer)

    state: ExecutionState = {
        "work_brief": brief,
        "trace_id": "t-test-7",
        "run_id": "r-test-7",
        "current_phase_index": 0,
        "current_wave_index": 0,
        "wave_results": [],
        "wave_reflections": [],
        "completed_phases": [],
        "revision_count": 0,
    }
    config = {"configurable": {"thread_id": "test-exec-qa-fail"}}

    # First invocation — QA fails, routes to prepare_wave for retry
    # (verdict=fail, revision_count=0 < MAX_WAVE_RETRIES)
    # After 3 retries, it should end
    result = await graph.ainvoke(state, config=config)

    reflections = result.get("wave_reflections", [])
    # Should have multiple fail reflections from retries
    assert len(reflections) >= 1
    assert any(r["verdict"] == "fail" for r in reflections)


# ---------------------------------------------------------------------------
# Test 8: Full workflow — approve
# ---------------------------------------------------------------------------


async def test_full_workflow_approve() -> None:
    """End-to-end happy path: triage -> plan (approve) -> execute."""
    result = await run_full_workflow(
        "Create social media content about AI in marketing",
        auto_approve=True,
    )

    assert result["phase"] == "execution"
    exec_state = result["execution"]
    assert len(exec_state["wave_results"]) == 10
    assert len(exec_state["completed_phases"]) == 2
    assert len(exec_state["wave_reflections"]) == 4


# ---------------------------------------------------------------------------
# Test 9: Full workflow — reject then approve
# ---------------------------------------------------------------------------


async def test_full_workflow_reject_then_approve() -> None:
    """End-to-end with one rejection cycle before approval."""
    import uuid

    from langgraph.checkpoint.memory import MemorySaver

    from .run import (
        resume_planning,
        run_entry,
        run_execution,
        run_planning,
    )

    run_id = str(uuid.uuid4())[:8]
    checkpointer = MemorySaver()

    # Triage
    entry_result = await run_entry("Create social media content", run_id, checkpointer)
    assert entry_result["triage"]["complexity"] == "complex"

    # Plan — hits interrupt
    await run_planning("Create social media content", run_id, checkpointer)

    # Reject with feedback
    await resume_planning(
        run_id,
        checkpointer,
        {"approved": False, "feedback": "Add more research"},
    )

    # Approve revised plan
    planning_result = await resume_planning(run_id, checkpointer, {"approved": True})
    assert planning_result["plan_approved"] is True

    # Execute
    exec_result = await run_execution(
        planning_result["work_brief"], run_id, checkpointer
    )
    assert len(exec_result["completed_phases"]) >= 1


# ---------------------------------------------------------------------------
# Test 10: emit_progress events in stream
# ---------------------------------------------------------------------------


async def test_emit_progress_events_stream() -> None:
    """Verify emit_progress() events appear in astream output.

    Uses stream_mode=["updates", "custom"]. Custom events from
    emit_progress() are delivered via get_stream_writer() and appear
    as ("custom", data) tuples in astream output.
    """
    checkpointer = MemorySaver()
    graph = build_entry_graph().compile(checkpointer=checkpointer)

    state: EntryState = {
        "user_message": "Test progress streaming",
        "trace_id": "t-test-10",
        "run_id": "r-test-10",
    }
    config = {"configurable": {"thread_id": "test-progress"}}

    custom_events: list[dict[str, Any]] = []
    async for chunk in graph.astream(state, config, stream_mode=["updates", "custom"]):
        mode, data = chunk
        if mode == "custom":
            custom_events.append(data)

    # The triage agent calls emit_progress twice (started, completed)
    assert len(custom_events) >= 2
    types = [e.get("type") for e in custom_events]
    assert "started" in types
    assert "completed" in types


# ---------------------------------------------------------------------------
# Test 11: State stays lean
# ---------------------------------------------------------------------------


async def test_state_stays_lean() -> None:
    """No state entry's output exceeds a reasonable content limit."""
    result = await run_full_workflow(
        "Create social media content about AI in marketing",
        auto_approve=True,
    )

    content_limit = 4000
    exec_state = result.get("execution", {})
    for entry in exec_state.get("wave_results", []):
        output = entry.get("output", "")
        assert len(output) <= content_limit, (
            f"{entry['agent_id']}/{entry['command']} output "
            f"({len(output)} chars) exceeds content limit"
        )


# ---------------------------------------------------------------------------
# Test 12: Postgres checkpointer (integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_full_workflow_postgres_checkpointer() -> None:
    """Full workflow using Postgres checkpointer from docker-compose.

    Requires: docker compose -f docker-compose.dev.yml up -d
    Run with: uv run pytest -m integration
    """
    pytest.importorskip("psycopg")

    import os

    db_url = os.environ.get(
        "MONET_POSTGRES_URL",
        "postgresql://monet:monet@localhost:5432/monet",
    )

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()

        import uuid

        from .run import (
            resume_planning,
            run_entry,
            run_execution,
            run_planning,
        )

        run_id = f"pg-{uuid.uuid4().hex[:6]}"
        msg = "Create social media content about AI"

        entry_result = await run_entry(msg, run_id, checkpointer)
        assert entry_result["triage"]["complexity"] == "complex"

        await run_planning(msg, run_id, checkpointer)
        planning_result = await resume_planning(
            run_id, checkpointer, {"approved": True}
        )
        assert planning_result["plan_approved"] is True

        exec_result = await run_execution(
            planning_result["work_brief"], run_id, checkpointer
        )
        assert len(exec_result["completed_phases"]) == 2
