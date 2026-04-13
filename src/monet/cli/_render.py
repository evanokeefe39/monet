"""Terminal rendering and NDJSON serialization for monet run events."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

import click

from monet.client._events import (
    AgentProgress,
    ExecutionInterrupt,
    PendingDecision,
    PlanApproved,
    PlanInterrupt,
    PlanReady,
    ReflectionComplete,
    RunComplete,
    RunDetail,
    RunEvent,
    RunFailed,
    RunSummary,
    TriageComplete,
    WaveComplete,
)


def render_event(event: RunEvent) -> None:
    """Pretty-print a run event to the terminal."""
    if isinstance(event, TriageComplete):
        click.secho(f"Triage: {event.complexity}", fg="cyan")
        if event.suggested_agents:
            click.secho(f"  Agents: {', '.join(event.suggested_agents)}", fg="cyan")

    elif isinstance(event, PlanReady):
        click.secho(f"Plan: {event.goal}", fg="green")
        for i, phase in enumerate(event.phases, 1):
            label = phase.get("label") or phase.get("name") or f"Phase {i}"
            click.echo(f"  {i}. {label}")
        if event.assumptions:
            click.secho("  Assumptions:", dim=True)
            for a in event.assumptions:
                click.secho(f"    - {a}", dim=True)

    elif isinstance(event, PlanApproved):
        click.secho("Plan approved", fg="green")

    elif isinstance(event, PlanInterrupt):
        click.secho("Plan awaiting approval:", fg="yellow", bold=True)
        brief = event.brief
        if brief.get("goal"):
            click.echo(f"  Goal: {brief['goal']}")
        for i, phase in enumerate(brief.get("phases") or [], 1):
            label = phase.get("label") or phase.get("name") or f"Phase {i}"
            click.echo(f"  {i}. {label}")

    elif isinstance(event, AgentProgress):
        click.secho(f"  [{event.agent_id}] {event.status}", dim=True)
        if event.reasons:
            click.secho(f"    {event.reasons}", fg="red", dim=True)

    elif isinstance(event, WaveComplete):
        click.secho(
            f"Wave {event.wave_index} complete ({len(event.results)} result(s))",
            fg="green",
        )

    elif isinstance(event, ReflectionComplete):
        color = "green" if event.verdict == "pass" else "yellow"
        click.secho(f"QA: {event.verdict}", fg=color)
        if event.notes:
            click.echo(f"  {event.notes}")

    elif isinstance(event, ExecutionInterrupt):
        click.secho(f"Execution paused: {event.reason}", fg="yellow", bold=True)
        click.echo(f"  Phase {event.phase_index}, Wave {event.wave_index}")

    elif isinstance(event, RunComplete):
        click.secho("Done.", fg="green", bold=True)
        for wr in event.wave_results:
            for art in wr.get("artifacts") or []:
                url = art.get("url", "")
                if url:
                    click.secho(f"  {url}", dim=True)

    elif isinstance(event, RunFailed):
        click.secho(f"Failed: {event.error}", fg="red", bold=True)


def prompt_plan_decision() -> Literal["approve", "revise", "reject"]:
    """Prompt the user for a plan approval decision.

    Returns:
        One of ``"approve"``, ``"revise"``, or ``"reject"``.
    """
    click.echo()
    choice = click.prompt(
        "Action",
        type=click.Choice(["approve", "revise", "reject"], case_sensitive=False),
        default="approve",
    )
    return choice  # type: ignore[no-any-return]


def prompt_execution_decision() -> Literal["retry", "abort"]:
    """Prompt the user for an execution interrupt decision.

    Returns:
        One of ``"retry"`` or ``"abort"``.
    """
    click.echo()
    choice = click.prompt(
        "Action",
        type=click.Choice(["retry", "abort"], case_sensitive=False),
        default="retry",
    )
    return choice  # type: ignore[no-any-return]


# ── NDJSON serialization ───────────────────────────────────────────


_CAMEL_TO_SNAKE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _event_type_name(event: RunEvent) -> str:
    """Convert event class name to snake_case for the ``type`` field.

    ``TriageComplete`` -> ``triage_complete``.
    """
    return _CAMEL_TO_SNAKE.sub("_", type(event).__name__).lower()


def serialize_event(event: RunEvent) -> str:
    """Serialize a ``RunEvent`` to a single NDJSON line.

    Uses ``default=str`` to handle non-JSON-serializable values
    (datetime, bytes, custom objects) in ``dict[str, Any]`` fields
    that carry untrusted agent outputs.
    """
    payload: dict[str, Any] = {"type": _event_type_name(event)}
    payload.update(dataclasses.asdict(event))
    return json.dumps(payload, default=str, ensure_ascii=False)


# ── Table rendering for monet runs ─────────────────────────────────


def format_age(iso_timestamp: str) -> str:
    """Convert an ISO-8601 timestamp to a human-friendly relative age.

    Returns strings like ``"2m ago"``, ``"3h ago"``, ``"5d ago"``.
    Returns ``""`` if the timestamp cannot be parsed.
    """
    if not iso_timestamp:
        return ""
    try:
        # Handle both Z-suffix and +00:00 offset formats.
        ts = iso_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        delta = datetime.now(UTC) - dt
        seconds = int(delta.total_seconds())
    except (ValueError, TypeError):
        return ""

    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def render_run_table(summaries: list[RunSummary]) -> None:
    """Render a table of run summaries to stdout."""
    if not summaries:
        click.echo("No runs found.")
        return

    click.echo(f"{'RUN ID':<12} {'STATUS':<14} {'PHASE':<12} {'AGE'}")
    click.echo("-" * 48)
    for s in summaries:
        age = format_age(s.created_at)
        click.echo(f"{s.run_id:<12} {s.status:<14} {s.phase:<12} {age}")


def render_pending_table(decisions: list[PendingDecision]) -> None:
    """Render a table of pending HITL decisions to stdout."""
    if not decisions:
        click.echo("No pending decisions.")
        return

    click.echo(f"{'RUN ID':<12} {'TYPE':<20} {'SUMMARY'}")
    click.echo("-" * 56)
    for d in decisions:
        click.echo(f"{d.run_id:<12} {d.decision_type:<20} {d.summary}")


def render_run_detail(detail: RunDetail) -> None:
    """Render full run detail to stdout."""
    click.secho(f"Run {detail.run_id}", bold=True)
    click.echo(f"  Status: {detail.status}")
    click.echo(f"  Phase:  {detail.phase}")

    if detail.triage:
        click.echo()
        click.secho("Triage", bold=True)
        complexity = detail.triage.get("complexity", "unknown")
        click.echo(f"  Complexity: {complexity}")
        agents = detail.triage.get("suggested_agents") or []
        if agents:
            click.echo(f"  Agents: {', '.join(agents)}")

    if detail.work_brief:
        click.echo()
        click.secho("Plan", bold=True)
        goal = detail.work_brief.get("goal", "")
        if goal:
            click.echo(f"  Goal: {goal}")
        for i, phase in enumerate(detail.work_brief.get("phases") or [], 1):
            label = phase.get("label") or phase.get("name") or f"Phase {i}"
            click.echo(f"  {i}. {label}")

    if detail.wave_results:
        click.echo()
        click.secho("Results", bold=True)
        for wr in detail.wave_results:
            agent = wr.get("agent_id", "?")
            command = wr.get("command", "?")
            click.echo(f"  [{agent}/{command}]")
            output = wr.get("output")
            if output:
                text = str(output)[:200]
                click.secho(f"    {text}", dim=True)
            for art in wr.get("artifacts") or []:
                url = art.get("url", "")
                if url:
                    click.secho(f"    {url}", dim=True)

    if detail.wave_reflections:
        click.echo()
        click.secho("QA", bold=True)
        for ref in detail.wave_reflections:
            verdict = ref.get("verdict", "")
            notes = ref.get("notes", "")
            color = "green" if verdict == "pass" else "yellow"
            click.secho(f"  {verdict}", fg=color)
            if notes:
                click.echo(f"    {notes}")
