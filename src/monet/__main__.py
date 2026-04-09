"""CLI entry point for monet — wires infrastructure and runs the message.

Usage:
    python -m monet "Write a post about AI"
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys


async def _main(message: str) -> None:
    from monet.server import bootstrap

    import monet.agents  # noqa: F401 — registers reference agents

    worker_task = await bootstrap(enable_tracing=True)

    try:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from monet.orchestration import (
            build_entry_graph,
            build_execution_graph,
            build_planning_graph,
        )

        checkpointer = MemorySaver()
        thread_id = "cli-run"

        # Entry / triage
        entry = build_entry_graph().compile(checkpointer=checkpointer)
        entry_state = await entry.ainvoke(
            {"task": message, "trace_id": thread_id, "run_id": thread_id},
            config={"configurable": {"thread_id": f"{thread_id}-entry"}},
        )
        triage = entry_state.get("triage") or {}
        if triage.get("complexity") == "simple":
            print(json.dumps({"phase": "entry", "triage": triage}, indent=2))
            return

        # Planning with auto-approve
        planning = build_planning_graph().compile(checkpointer=checkpointer)
        planning_config = {"configurable": {"thread_id": f"{thread_id}-planning"}}
        await planning.ainvoke(
            {
                "task": message,
                "trace_id": thread_id,
                "run_id": thread_id,
                "revision_count": 0,
            },
            config=planning_config,
        )
        planning_state = await planning.ainvoke(
            Command(resume={"approved": True, "feedback": None}),
            config=planning_config,
        )
        if not planning_state.get("plan_approved"):
            print(json.dumps({"phase": "planning"}, indent=2))
            return

        # Execution
        execution = build_execution_graph().compile(checkpointer=checkpointer)
        await execution.ainvoke(
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
            config={"configurable": {"thread_id": f"{thread_id}-execution"}},
        )
        print(json.dumps({"phase": "execution"}, indent=2))
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task


def main() -> None:
    message = " ".join(sys.argv[1:]).strip()
    if not message:
        message = input("Enter message: ").strip()
    if not message:
        print("No message provided.")
        return
    asyncio.run(_main(message))


if __name__ == "__main__":
    main()
