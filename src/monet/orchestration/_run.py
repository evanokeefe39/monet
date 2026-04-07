# mypy: disable-error-code="call-overload"
"""Top-level run() sequencer chaining entry → planning → execution.

Uses MemorySaver for direct in-process invocation. HITL interrupts are
resumable within the same process only. For durable cross-process
resumption use LangGraph Server (langgraph dev / langgraph up) which
attaches PostgresSaver automatically per langgraph.json.
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from .entry_graph import build_entry_graph
from .execution_graph import build_execution_graph
from .planning_graph import build_planning_graph


async def run(
    message: str,
    *,
    thread_id: str | None = None,
    auto_approve: bool = True,
) -> dict[str, Any]:
    """Run a message through entry → planning → execution.

    Returns the final execution state. Compiles each graph with
    MemorySaver for direct invocation. HITL approval is auto-approved
    by default for non-interactive use.
    """
    thread_id = thread_id or f"run-{uuid.uuid4().hex[:8]}"
    checkpointer = MemorySaver()

    # Entry / triage
    entry = build_entry_graph().compile(checkpointer=checkpointer)
    entry_config: RunnableConfig = {"configurable": {"thread_id": f"{thread_id}-entry"}}
    entry_state = await entry.ainvoke(
        {"task": message, "trace_id": thread_id, "run_id": thread_id},
        config=entry_config,
    )
    triage = entry_state.get("triage") or {}
    complexity = triage.get("complexity", "complex")

    if complexity == "simple":
        return {"phase": "entry", "triage": triage}

    # Planning with HITL
    planning = build_planning_graph().compile(checkpointer=checkpointer)
    planning_config: RunnableConfig = {
        "configurable": {"thread_id": f"{thread_id}-planning"}
    }
    await planning.ainvoke(
        {
            "task": message,
            "trace_id": thread_id,
            "run_id": thread_id,
            "revision_count": 0,
        },
        config=planning_config,
    )
    if auto_approve:
        planning_state = await planning.ainvoke(
            Command(resume={"approved": True, "feedback": None}),
            config=planning_config,
        )
    else:
        planning_state = await planning.aget_state(planning_config)
        planning_state = planning_state.values

    if not planning_state.get("plan_approved"):
        return {"phase": "planning", "planning": planning_state}

    # Execution
    execution = build_execution_graph().compile(checkpointer=checkpointer)
    execution_config: RunnableConfig = {
        "configurable": {"thread_id": f"{thread_id}-execution"}
    }
    execution_state = await execution.ainvoke(
        {
            "work_brief": planning_state["work_brief"],
            "trace_id": thread_id,
            "run_id": thread_id,
            "current_phase_index": 0,
            "current_wave_index": 0,
            "wave_results": [],
            "wave_reflections": [],
            "completed_phases": [],
            "revision_count": 0,
        },
        config=execution_config,
    )
    return {"phase": "execution", "execution": execution_state, "triage": triage}
