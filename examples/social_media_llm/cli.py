"""Click entry point for the social_media_llm example.

Drives the three monet graphs running on a LangGraph server. The CLI
process holds zero graph state — it just sends inputs and resumes
interrupts via :mod:`workflow`.

Run with::

    cd examples/social_media_llm
    uv run langgraph dev                       # Terminal A
    uv run python -m examples.social_media_llm "AI in marketing"  # Terminal B
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any

# Allow running as ``python cli.py`` from inside the example dir by
# adding this dir to sys.path before the sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import click
from app import check_environment, check_server, configure_app
from client import create_thread, make_client
from display import (
    print_brief,
    print_env_status,
    print_header,
    print_reflections,
    print_summary,
    print_triage,
    print_wave_results,
)
from prompts import (
    prompt_execution_decision,
    prompt_planning_decision,
)
from workflow import run_execution, run_planning, run_triage

from monet._tracing import (
    RUN_ROOT_SPAN_NAME,
    configure_tracing,
    get_tracer,
    inject_trace_context,
)


def _planning_decision_callback(_brief: dict[str, Any]) -> dict[str, Any]:
    # The brief was already printed before the prompt was called, so the
    # callback itself is a no-arg input prompt.
    return prompt_planning_decision()


def _execution_decision_callback(reflection: dict[str, Any]) -> dict[str, Any]:
    if reflection:
        click.echo(f"\n  QA verdict: {reflection.get('verdict', '?')}")
        click.echo(f"  Notes: {reflection.get('notes', '')}")
    return prompt_execution_decision()


@click.command()
@click.argument("topic", nargs=-1)
@click.option(
    "--server-url",
    default="http://localhost:2024",
    envvar="MONET_LANGGRAPH_URL",
    show_default=True,
    help="LangGraph Server URL.",
)
@click.option(
    "--run-id",
    default=None,
    help="Override the generated run id (8 hex chars by default).",
)
def main(topic: tuple[str, ...], server_url: str, run_id: str | None) -> None:
    """Run the monet content workflow against TOPIC."""
    configure_app()
    missing = check_environment()
    print_header("Social Media Content Generator (LLM)")
    print_env_status(missing)
    if missing:
        raise click.ClickException(
            f"Missing required env keys: {', '.join(missing)}. Set them in .env."
        )

    topic_str = " ".join(topic).strip() or click.prompt("Topic", type=str)
    resolved_run_id = run_id or str(uuid.uuid4())[:8]

    asyncio.run(_run(server_url, topic_str, resolved_run_id))


async def _run(server_url: str, topic: str, run_id: str) -> None:
    client = make_client(server_url)
    if not await check_server(client):
        raise click.ClickException(
            f"LangGraph Server not reachable at {server_url}. "
            "Start it with: `uv run langgraph dev`"
        )

    # Open a single root span on the CLI side and capture its W3C
    # traceparent into a carrier dict. The span is closed immediately
    # — the carrier is what flows downstream. Every server-side graph
    # (entry / planning / execution) receives this carrier in run
    # metadata and re-attaches it before invoking any agent, so all
    # agent spans across all three graphs land under one Langfuse
    # trace keyed by this run_id.
    configure_tracing()
    _tracer = get_tracer("monet.cli")
    with _tracer.start_as_current_span(
        RUN_ROOT_SPAN_NAME,
        attributes={"monet.run_id": run_id, "monet.topic": topic[:200]},
    ):
        trace_carrier = inject_trace_context()

    # ── Phase 1 ───────────────────────────────────────────────────────
    print_header("Phase 1: Triage")
    triage_thread = await create_thread(client)
    triage = await run_triage(
        client, triage_thread, topic, run_id, trace_carrier=trace_carrier
    )
    print_triage(triage)
    if triage.get("complexity") == "simple":
        click.echo("\n  Simple request — no content generation needed.")
        return

    # ── Phase 2 ───────────────────────────────────────────────────────
    print_header("Phase 2: Planning")
    planning_thread = await create_thread(client)

    def _planning_cb(brief: dict[str, Any]) -> dict[str, Any]:
        if brief:
            print_brief(brief)
        return _planning_decision_callback(brief)

    planning_state = await run_planning(
        client,
        planning_thread,
        topic,
        run_id,
        decision_prompt=_planning_cb,
        trace_carrier=trace_carrier,
    )
    if not planning_state.get("plan_approved"):
        click.echo("\n  Plan not approved. Exiting.")
        return
    work_brief = planning_state.get("work_brief") or {}

    # ── Phase 3 ───────────────────────────────────────────────────────
    print_header("Phase 3: Execution")
    exec_thread = await create_thread(client)
    exec_state = await run_execution(
        client,
        exec_thread,
        work_brief,
        run_id,
        gate_prompt=_execution_decision_callback,
        trace_carrier=trace_carrier,
    )

    # ── Final summary ────────────────────────────────────────────────
    print_header("Complete")
    print_summary(run_id, work_brief, exec_state)
    from monet import get_catalogue

    await print_wave_results(exec_state.get("wave_results") or [], get_catalogue())
    print_reflections(exec_state.get("wave_reflections") or [])


if __name__ == "__main__":
    main()
