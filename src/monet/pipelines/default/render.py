"""Terminal rendering for default-pipeline events and run details."""

from __future__ import annotations

from typing import Any

import click

from monet.pipelines.default.events import (
    DefaultPipelineEvent,
    DefaultPipelineRunDetail,
    ExecutionInterrupt,
    PlanApproved,
    PlanInterrupt,
    PlanReady,
    ReflectionComplete,
    TriageComplete,
    WaveComplete,
)


def _format_node_line(node: dict[str, Any]) -> str:
    """One-line summary: ``id: agent/cmd ← deps``."""
    nid = node.get("id", "?")
    agent = node.get("agent_id", "?")
    command = node.get("command", "?")
    deps = node.get("depends_on") or []
    base = f"  {nid}: {agent}/{command}"
    if deps:
        base += f" ← {', '.join(deps)}"
    return base


def render_pipeline_event(event: DefaultPipelineEvent) -> None:
    """Pretty-print a default-pipeline event to the terminal."""
    if isinstance(event, TriageComplete):
        click.secho(f"Triage: {event.complexity}", fg="cyan")
        if event.suggested_agents:
            click.secho(f"  Agents: {', '.join(event.suggested_agents)}", fg="cyan")

    elif isinstance(event, PlanReady):
        click.secho(f"Plan: {event.goal}", fg="green")
        for node in event.nodes:
            click.echo(_format_node_line(node))

    elif isinstance(event, PlanApproved):
        click.secho("Plan approved", fg="green")

    elif isinstance(event, PlanInterrupt):
        click.secho("Plan awaiting approval:", fg="yellow", bold=True)
        goal = event.routing_skeleton.get("goal", "")
        if goal:
            click.echo(f"  Goal: {goal}")
        for node in event.routing_skeleton.get("nodes") or []:
            click.echo(_format_node_line(node))

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
        if event.pending_node_ids:
            click.echo(f"  Pending: {', '.join(event.pending_node_ids)}")


def render_pipeline_run_detail(detail: DefaultPipelineRunDetail) -> None:
    """Render a typed default-pipeline run detail to stdout."""
    click.secho(f"Run {detail.run_id}", bold=True)
    click.echo(f"  Status: {detail.status}")
    if detail.completed_stages:
        click.echo(f"  Stages: {', '.join(detail.completed_stages)}")

    if detail.triage:
        click.echo()
        click.secho("Triage", bold=True)
        complexity = detail.triage.get("complexity", "unknown")
        click.echo(f"  Complexity: {complexity}")
        agents = detail.triage.get("suggested_agents") or []
        if agents:
            click.echo(f"  Agents: {', '.join(agents)}")

    if detail.routing_skeleton:
        click.echo()
        click.secho("Plan", bold=True)
        goal = detail.routing_skeleton.get("goal", "")
        if goal:
            click.echo(f"  Goal: {goal}")
        for node in detail.routing_skeleton.get("nodes") or []:
            click.echo(_format_node_line(node))

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


def summary_for_tag(tag: str) -> str:
    """Friendly summary for a default-pipeline interrupt tag."""
    if tag == "human_approval":
        return "Plan awaiting approval"
    if tag == "human_interrupt":
        return "Execution paused — blocking signal or QA failure"
    return tag


__all__ = ["render_pipeline_event", "render_pipeline_run_detail", "summary_for_tag"]
