"""Top-level orchestrator — sequences entry, planning, and execution graphs.

Each graph gets its own thread_id for independent checkpointer state and
resumability. The planning graph's revision_count is NOT carried into
the execution graph — they are separate revision cycles.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from .entry_graph import build_entry_graph
from .execution_graph import build_execution_graph
from .planning_graph import build_planning_graph
from .state import EntryState, ExecutionState, PlanningState  # noqa: TC001


async def run_entry(
    user_message: str,
    run_id: str,
    checkpointer: MemorySaver,
) -> dict[str, Any]:
    """Run the entry/triage graph. Returns the full state."""
    graph = build_entry_graph().compile(checkpointer=checkpointer)
    state: EntryState = {
        "user_message": user_message,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
    }
    config = {"configurable": {"thread_id": run_id}}
    return await graph.ainvoke(state, config=config)


async def run_planning(
    user_message: str,
    run_id: str,
    checkpointer: MemorySaver,
) -> dict[str, Any] | None:
    """Run the planning graph with HITL approval.

    Returns the final planning state, or None if the first invocation
    hits the interrupt (caller must resume with Command).
    """
    graph = build_planning_graph().compile(checkpointer=checkpointer)
    state: PlanningState = {
        "user_message": user_message,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
        "revision_count": 0,
    }
    thread_id = f"{run_id}-planning"
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(state, config=config)


async def resume_planning(
    run_id: str,
    checkpointer: MemorySaver,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Resume the planning graph after HITL interrupt.

    Args:
        decision: {"approved": bool, "feedback": str | None}
    """
    graph = build_planning_graph().compile(checkpointer=checkpointer)
    thread_id = f"{run_id}-planning"
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(Command(resume=decision), config=config)


async def run_execution(
    work_brief: dict[str, Any],
    run_id: str,
    checkpointer: MemorySaver,
) -> dict[str, Any]:
    """Run the execution graph with an approved work brief.

    ExecutionState is initialized with revision_count=0 regardless
    of how many planning revisions occurred.
    """
    graph = build_execution_graph().compile(checkpointer=checkpointer)
    state: ExecutionState = {
        "work_brief": work_brief,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
        "current_phase_index": 0,
        "current_wave_index": 0,
        "wave_results": [],
        "wave_reflections": [],
        "completed_phases": [],
        "revision_count": 0,
    }
    thread_id = f"{run_id}-execution"
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(state, config=config)


async def resume_execution(
    run_id: str,
    checkpointer: MemorySaver,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Resume the execution graph after HITL interrupt.

    Args:
        decision: {"action": "continue"|"abort", "feedback": str|None}
    """
    graph = build_execution_graph().compile(checkpointer=checkpointer)
    thread_id = f"{run_id}-execution"
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(Command(resume=decision), config=config)


async def run_full_workflow(
    user_message: str,
    auto_approve: bool = True,
) -> dict[str, Any]:
    """Run the complete workflow end-to-end.

    If auto_approve is True, automatically approves the planning HITL gate.
    Used by automated tests.
    """
    run_id = str(uuid.uuid4())[:8]
    checkpointer = MemorySaver()

    # 1. Entry graph — triage
    entry_result = await run_entry(user_message, run_id, checkpointer)
    triage = entry_result.get("triage") or {}
    complexity = triage.get("complexity", "simple")

    if complexity != "complex":
        return {"phase": "entry", "triage": triage, "result": entry_result}

    # 2. Planning graph — build and approve work brief
    planning_result = await run_planning(user_message, run_id, checkpointer)

    # Planning graph hits interrupt at human_approval_node
    if auto_approve:
        planning_result = await resume_planning(
            run_id, checkpointer, {"approved": True}
        )

    if not planning_result or not planning_result.get("plan_approved"):
        return {
            "phase": "planning",
            "result": planning_result,
            "approved": False,
        }

    work_brief = planning_result.get("work_brief", {})

    # 3. Execution graph — wave-based execution
    execution_result = await run_execution(work_brief, run_id, checkpointer)

    return {
        "phase": "execution",
        "run_id": run_id,
        "entry": entry_result,
        "planning": planning_result,
        "execution": execution_result,
    }
