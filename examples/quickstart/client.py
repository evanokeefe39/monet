"""monet quickstart client — drive the content workflow via monet.client.

Connects to a running LangGraph server and runs the three-phase
pipeline: triage, planning (auto-approved), and execution.

Prerequisites:
    export GEMINI_API_KEY="..."
    export GROQ_API_KEY="..."

Usage:
    # Terminal 1: start the graph server
    cd examples/quickstart
    uv run langgraph dev

    # Terminal 2: run this client
    python examples/quickstart/client.py "AI trends in healthcare"
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from monet.client import (
    ENTRY_GRAPH,
    EXECUTION_GRAPH,
    PLANNING_GRAPH,
    create_thread,
    drain_stream,
    entry_input,
    execution_input,
    get_state_values,
    make_client,
    planning_input,
)


def _print_event(label: str, mode: str, data: Any) -> None:
    """Render streaming events to the console."""
    if mode == "custom" and isinstance(data, dict):
        agent = data.get("agent", "")
        status = data.get("status", "")
        if agent:
            print(f"    -> {agent}: {status}")
        elif status:
            print(f"    -> {status}")
    elif mode == "updates" and isinstance(data, dict):
        for node in data:
            if node not in ("__start__", "__interrupt__"):
                print(f"  [{label}] {node} complete")


async def run(topic: str, server_url: str = "http://localhost:2024") -> None:
    client = make_client(server_url)
    run_id = "quickstart"

    print(f"\n{'=' * 50}")
    print(f"  topic: {topic}")
    print(f"{'=' * 50}")

    # ── Phase 1: Triage ─────────────────────────────────────────────
    print("\n[1/3] triaging...")
    thread = await create_thread(client)
    await drain_stream(
        client,
        thread,
        ENTRY_GRAPH,
        "triage",
        input=entry_input(topic, run_id),
        on_event=_print_event,
    )

    values, _ = await get_state_values(client, thread)
    triage = values.get("triage") or {}
    complexity = triage.get("complexity", "unknown")
    agents = triage.get("suggested_agents") or []
    print(f"  complexity: {complexity}")
    if agents:
        print(f"  suggested agents: {', '.join(agents)}")
    if complexity == "simple":
        print("  simple request — done.")
        return

    # ── Phase 2: Planning ───────────────────────────────────────────
    print("\n[2/3] planning...")
    thread = await create_thread(client)
    await drain_stream(
        client,
        thread,
        PLANNING_GRAPH,
        "planning",
        input=planning_input(topic, run_id),
        on_event=_print_event,
    )

    # Check for HITL interrupt — auto-approve for the quickstart.
    values, nxt = await get_state_values(client, thread)
    if "human_approval" in nxt:
        await drain_stream(
            client,
            thread,
            PLANNING_GRAPH,
            "planning",
            command={"resume": {"approved": True}},
            on_event=_print_event,
        )
        values, _ = await get_state_values(client, thread)

    if not values.get("plan_approved"):
        print("  plan not approved.")
        return

    brief = values.get("work_brief") or {}
    phases = brief.get("phases") or []
    print(f"  goal: {brief.get('goal', 'N/A')}")
    print(f"  phases: {len(phases)}")
    for i, phase in enumerate(phases):
        waves = phase.get("waves") or []
        items = sum(len(w.get("items") or []) for w in waves)
        print(f"    {i + 1}. {phase.get('name', '?')} ({items} agent calls)")

    # ── Phase 3: Execution ──────────────────────────────────────────
    print("\n[3/3] executing...")
    thread = await create_thread(client)
    await drain_stream(
        client,
        thread,
        EXECUTION_GRAPH,
        "execution",
        input=execution_input(brief, run_id),
        on_event=_print_event,
    )

    values, _ = await get_state_values(client, thread)
    wave_results = values.get("wave_results") or []
    reflections = values.get("wave_reflections") or []

    # ── Results ─────────────────────────────────────────────────────
    print(f"\n{'=' * 50}")
    print(f"  results: {len(wave_results)} agent outputs")
    print(f"  qa reflections: {len(reflections)}")
    print(f"{'=' * 50}")

    for wr in wave_results:
        aid = wr.get("agent_id", "?")
        cmd = wr.get("command", "?")
        print(f"\n  [{aid}/{cmd}]")

        signals = wr.get("signals") or []
        failures = [
            s
            for s in signals
            if s.get("type", "").endswith("_error")
            or s.get("type") in ("semantic_error", "needs_human_review")
        ]
        if failures:
            for s in failures:
                print(f"    !! {s.get('type')}: {(s.get('reason') or '')[:100]}")
            continue

        artifacts = wr.get("artifacts") or []
        if artifacts:
            for ptr in artifacts:
                aid_short = (ptr.get("artifact_id") or "")[:8]
                print(f"    artifact {aid_short}...")
            continue

        output = wr.get("output")
        if output:
            print(f"    {str(output)[:200]}")

    for ref in reflections:
        verdict = ref.get("verdict", "?")
        notes = ref.get("notes", "")
        print(f"\n  qa: {verdict} — {notes[:100]}")

    print("\ndone.")


def main() -> None:
    topic = " ".join(sys.argv[1:]).strip()
    if not topic:
        topic = input("topic: ").strip()
    if not topic:
        print("no topic provided.")
        sys.exit(1)
    asyncio.run(run(topic))


if __name__ == "__main__":
    main()
