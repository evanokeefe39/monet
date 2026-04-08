"""Reference client for the monet SDK reference stack.

Run with: python -m examples.social_media_llm "your topic here"

Demonstrates how to drive the monet SDK's built-in agents and graphs
from a custom terminal client:

  - imports the reference agents via `import monet.agents`
  - wires the three graphs (entry -> planning -> execution) from
    `monet.orchestration`
  - consumes `emit_progress()` events via astream(stream_mode=[...custom])
  - handles HITL interrupts for planning approval and execution QA gates
    via Command(resume={...})

The manual graph wiring is intentional — it is what makes the streaming
progress visible. `monet.orchestration.run()` is a non-streaming
sequencer and is unsuitable for an interactive terminal client.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

# Load environment variables before importing agents
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

load_dotenv()  # loads from .env in repo root

from monet import get_catalogue  # noqa: E402
from monet.catalogue import (  # noqa: E402
    CatalogueService,
    FilesystemStorage,
    SQLiteIndex,
    configure_catalogue,
)

_catalogue_root = Path(os.environ.get("CATALOGUE_ROOT", ".catalogue"))
_catalogue_db = os.environ.get(
    "CATALOGUE_DB_URL", f"sqlite+aiosqlite:///{_catalogue_root / 'index.db'}"
)
_catalogue_root.mkdir(parents=True, exist_ok=True)
configure_catalogue(
    CatalogueService(
        storage=FilesystemStorage(root=_catalogue_root / "artifacts"),
        index=SQLiteIndex(db_url=_catalogue_db),
    )
)

# Import reference agents to trigger @agent registration (AFTER env loading)
import monet.agents  # noqa: F401, E402 — registers reference agents
from monet.orchestration import (  # noqa: E402
    EntryState,
    ExecutionState,
    PlanningState,
    build_entry_graph,
    build_execution_graph,
    build_planning_graph,
)

_ARTIFACT_REPR_RE = re.compile(r"artifact_id='([^']+)'")
_UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def _extract_artifact_id(output: str) -> str | None:
    """Extract artifact_id from ArtifactPointer repr or URL string."""
    # ArtifactPointer(artifact_id='UUID', url='...')
    m = _ARTIFACT_REPR_RE.search(output)
    if m:
        return m.group(1)
    # file://.catalogue/artifacts/UUID/content or memory://UUID
    if output.strip().startswith(("file://", "memory://")):
        m = _UUID_RE.search(output)
        if m:
            return m.group(1)
    return None


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


def _print_env_status() -> None:
    """Print which API keys are configured."""
    keys = {
        "GEMINI_API_KEY": "Gemini (planner, researcher, writer)",
        "GROQ_API_KEY": "Groq (QA)",
        "TAVILY_API_KEY": "Tavily (web search)",
    }
    print("  Configured providers:")
    for key, desc in keys.items():
        status = "ok" if os.environ.get(key) else "MISSING"
        print(f"    {desc}: {status}")
    print()


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
    """Run a graph with streaming output."""
    final_state: dict[str, Any] = {}

    async for chunk in graph.astream(
        input_state, config, stream_mode=["updates", "custom"]
    ):
        mode, data = chunk

        if mode == "custom":
            status = data.get("status", "")
            agent_name = data.get("agent", "")
            if agent_name:
                print(f"    -> {agent_name}: {status}")
            else:
                print(f"    -> {status}")

        elif mode == "updates":
            for node_name, update in data.items():
                if node_name in ("__start__", "__interrupt__"):
                    continue
                print(f"  [{label}] {node_name} complete")
                if isinstance(update, dict):
                    final_state.update(update)

    return final_state


async def main() -> None:
    """Run the interactive LLM-backed content workflow."""
    _print_header("Social Media Content Generator (LLM)")
    _print_env_status()

    # Check required keys
    missing = [
        k
        for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "TAVILY_API_KEY")
        if not os.environ.get(k)
    ]
    if missing:
        print(f"  ERROR: Missing API keys: {', '.join(missing)}")
        print("  Set them in .env or load from ~/repos/deepagents-sandpit/.env")
        return

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
        "task": topic,
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
    agents_list = triage.get("suggested_agents", [])
    print(f"  Suggested agents: {', '.join(agents_list)}")

    if complexity == "simple":
        print("\n  Simple request — no content generation needed.")
        return

    if complexity == "bounded":
        # Bounded: run a single writer agent directly and show the result
        _print_header("Bounded: Single Agent Execution")
        from monet.orchestration import invoke_agent

        try:
            result = await invoke_agent(
                "writer",
                command="deep",
                task=f"Write a social media post about: {topic}",
                trace_id=f"trace-{run_id}",
                run_id=run_id,
            )
        except LookupError:
            result = None
        if result is not None:
            output = (
                result.output
                if isinstance(result.output, str)
                else "(content written to catalogue)"
            )
            print(f"  {output}")
        print()
        override = (
            input("  Run full multi-platform workflow instead? [y/n] > ")
            .strip()
            .lower()
        )
        if override != "y":
            _print_header("Complete")
            print(f"  Run ID: {run_id}")
            print("  Mode: bounded (single agent)")
            return
        print("  Proceeding to full workflow.")

    # ---------------------------------------------------------------
    # 2. Planning graph — build and approve work brief
    # ---------------------------------------------------------------
    _print_header("Phase 2: Planning")

    planning_graph = build_planning_graph().compile(checkpointer=checkpointer)
    planning_state: PlanningState = {
        "task": topic,
        "trace_id": f"trace-{run_id}",
        "run_id": run_id,
        "revision_count": 0,
    }
    thread_id_planning = f"{run_id}-planning"
    planning_config = {"configurable": {"thread_id": thread_id_planning}}

    await _run_with_streaming(
        planning_graph, planning_state, planning_config, "planning"
    )

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
            print("\n  Replanning with feedback...")
        else:
            break

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
    catalogue = get_catalogue()
    for wr in wave_results:
        output = str(wr.get("output", ""))
        pi = wr.get("phase_index")
        wi = wr.get("wave_index")
        ii = wr.get("item_index")
        aid = wr.get("agent_id")
        cmd = wr.get("command")
        print(f"\n    [{pi}.{wi}.{ii}] {aid}/{cmd}:")

        # Resolve catalogue artifacts to show full content.
        artifact_id = _extract_artifact_id(output)
        if artifact_id:
            try:
                content, meta = await catalogue.read(artifact_id)
                print(
                    f"    [artifact {artifact_id[:8]}... "
                    f"type={meta['content_type']} "
                    f"size={meta['content_length']}b]"
                )
                text = content.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    print(f"      {line}")
            except (KeyError, ValueError) as e:
                print(f"    (could not read artifact: {e})")
        else:
            print(f"    {output[:200]}")

    print("\n  QA reflections:")
    for ref in reflections:
        print(
            f"    Phase {ref.get('phase_index')}, "
            f"Wave {ref.get('wave_index')}: "
            f"{ref.get('verdict')} -- {ref.get('notes', '')}"
        )


def cli_main() -> None:
    """Entry point for the CLI."""
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
