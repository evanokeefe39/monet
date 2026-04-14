"""Terminal rendering and NDJSON serialization for monet run events.

Renders the graph-agnostic core events (:mod:`monet.client._events`).
Default-pipeline domain events render via
:mod:`monet.pipelines.default.render`.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

import click

from monet.client._events import (
    AgentProgress,
    Interrupt,
    NodeUpdate,
    PendingDecision,
    RunComplete,
    RunEvent,
    RunFailed,
    RunStarted,
    RunSummary,
    SignalEmitted,
)


def render_event(event: RunEvent) -> None:
    """Pretty-print a core run event to the terminal."""
    if isinstance(event, RunStarted):
        click.secho(f"Run {event.run_id} on {event.graph_id}", dim=True)

    elif isinstance(event, NodeUpdate):
        # Quiet by default — node deltas are noisy. Show as dim one-liner.
        keys = ", ".join(sorted(event.update)) or "(no keys)"
        click.secho(f"  → {event.node} [{keys}]", dim=True)

    elif isinstance(event, AgentProgress):
        click.secho(f"  [{event.agent_id}] {event.status}", dim=True)
        if event.reasons:
            click.secho(f"    {event.reasons}", fg="red", dim=True)

    elif isinstance(event, SignalEmitted):
        click.secho(
            f"  [{event.agent_id}] signal: {event.signal_type}", fg="yellow", dim=True
        )

    elif isinstance(event, Interrupt):
        click.secho(f"Paused at {event.tag}", fg="yellow", bold=True)

    elif isinstance(event, RunComplete):
        click.secho("Done.", fg="green", bold=True)

    elif isinstance(event, RunFailed):
        click.secho(f"Failed: {event.error}", fg="red", bold=True)


def prompt_plan_decision() -> Literal["approve", "revise", "reject"]:
    """Prompt the user for a plan approval decision."""
    click.echo()
    choice = click.prompt(
        "Action",
        type=click.Choice(["approve", "revise", "reject"], case_sensitive=False),
        default="approve",
    )
    return choice  # type: ignore[no-any-return]


def prompt_execution_decision() -> Literal["retry", "abort"]:
    """Prompt the user for an execution interrupt decision."""
    click.echo()
    choice = click.prompt(
        "Action",
        type=click.Choice(["retry", "abort"], case_sensitive=False),
        default="retry",
    )
    return choice  # type: ignore[no-any-return]


# ── NDJSON serialization ───────────────────────────────────────────


_CAMEL_TO_SNAKE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _event_type_name(event: object) -> str:
    """Convert event class name to snake_case for the ``type`` field."""
    return _CAMEL_TO_SNAKE.sub("_", type(event).__name__).lower()


def serialize_event(event: object) -> str:
    """Serialize any dataclass event to a single NDJSON line.

    Uses ``default=str`` to handle non-JSON-serializable values
    (datetime, bytes, custom objects) in ``dict[str, Any]`` fields
    that carry untrusted agent outputs.
    """
    payload: dict[str, Any] = {"type": _event_type_name(event)}
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        payload.update(dataclasses.asdict(event))
    return json.dumps(payload, default=str, ensure_ascii=False)


# ── Table rendering for monet runs ─────────────────────────────────


def format_age(iso_timestamp: str) -> str:
    """Convert an ISO-8601 timestamp to a human-friendly relative age."""
    if not iso_timestamp:
        return ""
    try:
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

    click.echo(f"{'RUN ID':<12} {'STATUS':<14} {'STAGES':<24} {'AGE'}")
    click.echo("-" * 60)
    for s in summaries:
        age = format_age(s.created_at)
        stages = ",".join(s.completed_stages) or "-"
        click.echo(f"{s.run_id:<12} {s.status:<14} {stages:<24} {age}")


def render_pending_table(decisions: list[PendingDecision]) -> None:
    """Render a table of pending HITL decisions to stdout."""
    if not decisions:
        click.echo("No pending decisions.")
        return

    click.echo(f"{'RUN ID':<12} {'TAG':<20} {'SUMMARY'}")
    click.echo("-" * 56)
    for d in decisions:
        click.echo(f"{d.run_id:<12} {d.decision_type:<20} {d.summary}")
