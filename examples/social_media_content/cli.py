"""Interactive terminal client for the social media content workflow.

Run with: uv run python -m examples.social_media_content.cli

Demonstrates:
  - Three-graph supervisor topology (entry -> planning -> execution)
  - HITL approve/reject/feedback at planning and execution gates
  - Streaming progress via astream_events with emit_progress()
  - Wave-based parallel execution visible in output
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

# Import agents to trigger registration
from . import agents as _agents  # noqa: F401
from .entry_graph import build_entry_graph
from .execution_graph import build_execution_graph
from .planning_graph import build_planning_graph
from .state import EntryState, ExecutionState, PlanningState  # noqa: TC001


def _print_header(text: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def _print_brief(brief: dict[str, Any]) -> None:
    print(f"  Goal: {brief.get('goal', 'N/A')}")
    print(f"  In scope: {', '.join(brief.get('in_scope', []))}")
    print(f"  Out of scope: {', '.join(brief.get('out_of_scope', []))}")
    phases = brief.get("phases", [])
    print(f"  Phases ({len(phases)}):")
    for i, phase in enumerate(phases):
        wave_count = len(phase.get("waves", []))
        item_count = sum(len(w.get("items", [])) for w in phase.get("waves", []))
        name = phase.get("name", "?")
        print(f"    {i + 1}. {name} ({wave_count} waves, {item_count} items)")
    print(f"  Assumptions: {', '.join(brief.get('assumptions', []))}")
    criteria = brief.get("quality_criteria", {})
    if criteria:
        print("  Quality criteria:")
        for k, v in criteria.items():
            print(f"    - {k}: {v}")


def _prompt_decision() -> dict[str, Any]:
    """Prompt user for approve/reject/feedback decision."""
    while True:
        print('\n  [a]pprove / [r]eject / [f]eedback "your notes"')
        raw = input("  > ").strip()
        if not raw:
            continue
        if raw.lower().startswith("a"):
            return {"approved": True}
        if raw.lower().startswith("r"):
            return {"approved": False, "feedback": None}
        if raw.lower().startswith("f"):
            feedback = raw[1:].strip().strip('"').strip("'").strip()
            if not feedback:
                feedback = raw[len("feedback") :].strip().strip('"').strip("'").strip()
            if not feedback:
                print("  Please provide feedback text.")
                continue
            return {"approved": False, "feedback": feedback}
        print("  Invalid input. Try again.")


def _prompt_execution_decision() -> dict[str, Any]:
    """Prompt user for continue/abort decision at execution HITL."""
    while True:
        print('\n  [c]ontinue / [a]bort / [f]eedback "your notes"')
        raw = input("  > ").strip()
        if not raw:
            continue
        if raw.lower().startswith("c"):
            return {"action": "continue"}
        if raw.lower().startswith("a"):
            return {"action": "abort", "feedback": "Aborted by user"}
        if raw.lower().startswith("f"):
            feedback = raw[1:].strip().strip('"').strip("'").strip()
            if not feedback:
                feedback = raw[len("feedback") :].strip().strip('"').strip("'").strip()
            return {"action": "continue", "feedback": feedback}
        print("  Invalid input. Try again.")


async def _run_with_streaming(
    graph: Any,
    input_state: dict[str, Any],
    config: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """Run a graph with streaming output.

    Uses astream with stream_mode=["updates", "custom"] to capture
    both node updates and custom emit_progress() events.
    """
    final_state: dict[str, Any] = {}

    async for chunk in graph.astream(
        input_state, config, stream_mode=["updates", "custom"]
    ):
        mode, data = chunk

        if mode == "custom":
            evt_type = data.get("type", "progress")
            agent_id = data.get("agent_id", "?")
            cmd = data.get("command", "?")
            print(f"    -> {evt_type}: {agent_id}/{cmd}")

        elif mode == "updates":
            for node_name, update in data.items():
                if node_name in ("__start__", "__interrupt__"):
                    continue
                print(f"  [{label}] {node_name} complete")
                if isinstance(update, dict):
                    final_state.update(update)

    return final_state


async def main() -> None:
    """Run the interactive social media content workflow."""
    _print_header("Social Media Content Generator")

    # Get topic from user
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
    else:
        topic = input("  Enter content topic: ").strip()
        if not topic:
            topic = "AI in marketing"
            print(f"  Using default topic: {topic}")

    run_id = str(uuid.uuid4())[:8]
    checkpointer = MemorySaver()

    # ---------------------------------------------------------------
    # 1. Entry graph — triage
    # ---------------------------------------------------------------
    _print_header("Phase 1: Triage")

    entry_graph = build_entry_graph().compile(checkpointer=checkpointer)
    entry_state: EntryState = {
        "user_message": topic,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
    }
    entry_config = {"configurable": {"thread_id": run_id}}
    entry_result = await _run_with_streaming(
        entry_graph, entry_state, entry_config, "triage"
    )

    triage = entry_result.get("triage", {})
    complexity = triage.get("complexity", "simple")
    print(f"\n  Triage result: complexity={complexity}")
    print(f"  Suggested agents: {', '.join(triage.get('suggested_agents', []))}")

    if complexity != "complex":
        print("\n  Simple request -- returning direct result.")
        return

    # ---------------------------------------------------------------
    # 2. Planning graph — build and approve work brief
    # ---------------------------------------------------------------
    _print_header("Phase 2: Planning")

    planning_graph = build_planning_graph().compile(checkpointer=checkpointer)
    planning_state: PlanningState = {
        "user_message": topic,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
        "revision_count": 0,
    }
    thread_id_planning = f"{run_id}-planning"
    planning_config = {"configurable": {"thread_id": thread_id_planning}}

    await _run_with_streaming(
        planning_graph, planning_state, planning_config, "planning"
    )

    # Planning graph hits interrupt at human_approval_node
    # The state may not have work_brief populated in the return from
    # astream_events when interrupted. Get it from the graph state.
    graph_state = await planning_graph.aget_state(planning_config)

    max_rounds = 5
    for _round in range(max_rounds):
        state_values = graph_state.values if graph_state else {}
        brief = state_values.get("work_brief", {})

        if state_values.get("plan_approved"):
            print("\n  Plan already approved.")
            break

        if brief:
            print("\n  --- Work Brief ---")
            _print_brief(brief)
            print("  --- End Brief ---")

        # Check if we're at an interrupt
        next_tasks = graph_state.next if graph_state else ()
        if "human_approval" in next_tasks:
            decision = _prompt_decision()

            await _run_with_streaming(
                planning_graph,
                Command(resume=decision),
                planning_config,
                "planning",
            )
            graph_state = await planning_graph.aget_state(planning_config)

            state_values = graph_state.values if graph_state else {}
            if state_values.get("plan_approved"):
                print("\n  Plan approved.")
                break
            if decision.get("approved") is False and not decision.get("feedback"):
                print("\n  Plan rejected. Exiting.")
                return
            # If feedback was given, loop continues — planner revises
            print("\n  Replanning with feedback...")
        else:
            break

    # Get final state
    final_planning = graph_state.values if graph_state else {}
    if not final_planning.get("plan_approved"):
        print("\n  Plan not approved. Exiting.")
        return

    work_brief = final_planning.get("work_brief", {})

    # ---------------------------------------------------------------
    # 3. Execution graph — wave-based execution
    # ---------------------------------------------------------------
    _print_header("Phase 3: Execution")

    execution_graph = build_execution_graph().compile(checkpointer=checkpointer)
    exec_state: ExecutionState = {
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
    thread_id_exec = f"{run_id}-execution"
    exec_config = {"configurable": {"thread_id": thread_id_exec}}

    await _run_with_streaming(execution_graph, exec_state, exec_config, "execution")

    # Handle possible execution interrupts
    exec_graph_state = await execution_graph.aget_state(exec_config)
    next_tasks = exec_graph_state.next if exec_graph_state else ()

    while "human_interrupt" in next_tasks:
        state_values = exec_graph_state.values if exec_graph_state else {}
        reflections = state_values.get("wave_reflections", [])
        if reflections:
            last = reflections[-1]
            print(f"\n  QA verdict: {last.get('verdict', '?')}")
            print(f"  Notes: {last.get('notes', '')}")

        decision = _prompt_execution_decision()
        await _run_with_streaming(
            execution_graph,
            Command(resume=decision),
            exec_config,
            "execution",
        )
        exec_graph_state = await execution_graph.aget_state(exec_config)
        next_tasks = exec_graph_state.next if exec_graph_state else ()

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    _print_header("Complete")

    final_exec = exec_graph_state.values if exec_graph_state else {}
    wave_results = final_exec.get("wave_results", [])
    completed = final_exec.get("completed_phases", [])
    reflections = final_exec.get("wave_reflections", [])

    print(f"  Run ID: {run_id}")
    print(f"  Phases completed: {len(completed)}/{len(work_brief.get('phases', []))}")
    print(f"  Total agent invocations: {len(wave_results)}")
    print(f"  QA reflections: {len(reflections)}")

    if final_exec.get("abort_reason"):
        print(f"  Aborted: {final_exec['abort_reason']}")

    print("\n  Wave results:")
    for wr in wave_results:
        output_preview = str(wr.get("output", ""))[:80]
        pi = wr.get("phase_index")
        wi = wr.get("wave_index")
        ii = wr.get("item_index")
        aid = wr.get("agent_id")
        cmd = wr.get("command")
        print(f"    [{pi}.{wi}.{ii}] {aid}/{cmd}: {output_preview}...")

    print("\n  QA reflections:")
    for ref in reflections:
        print(
            f"    Phase {ref.get('phase_index')}, Wave {ref.get('wave_index')}: "
            f"{ref.get('verdict')} — {ref.get('notes', '')}"
        )


def cli_main() -> None:
    """Entry point for the CLI."""
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
