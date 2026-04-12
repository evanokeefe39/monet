"""Terminal rendering for monet run events."""

from __future__ import annotations

from typing import Literal

import click

from monet.client._events import (
    AgentProgress,
    ExecutionInterrupt,
    PlanApproved,
    PlanInterrupt,
    PlanReady,
    ReflectionComplete,
    RunComplete,
    RunEvent,
    RunFailed,
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
