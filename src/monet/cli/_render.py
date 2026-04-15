"""Terminal rendering and NDJSON serialization for monet run events.

Renders the graph-agnostic core events (:mod:`monet.client._events`).
Form-schema interrupts render via :func:`render_interrupt_form`.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime
from typing import Any

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


def render_interrupt_form(values: dict[str, Any]) -> dict[str, Any]:
    """Render an interrupt form-schema envelope and collect a resume payload.

    Walks ``values["fields"]`` and prompts per-type. If ``values`` does
    not match the form-schema convention (no ``fields`` key), falls back
    to a JSON dump and a free-text JSON prompt — every interrupt remains
    answerable, just with degraded UX.

    Returns the dict to pass as the resume payload.
    """
    if not isinstance(values, dict) or "fields" not in values:
        click.echo()
        click.secho("Interrupt payload (no form schema):", fg="yellow", bold=True)
        click.echo(json.dumps(values, indent=2, default=str, ensure_ascii=False))
        raw = click.prompt("Resume payload (JSON)", default="{}")
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {}

    if prompt_text := values.get("prompt"):
        click.echo()
        click.secho(prompt_text, bold=True)

    context = values.get("context")
    if isinstance(context, dict) and context:
        click.secho("Context:", dim=True)
        click.echo(json.dumps(context, indent=2, default=str, ensure_ascii=False))
        click.echo()

    payload: dict[str, Any] = {}
    for field_spec in values.get("fields") or []:
        if not isinstance(field_spec, dict):
            continue
        name = field_spec.get("name")
        if not isinstance(name, str):
            continue
        payload[name] = _prompt_field(field_spec)
    return payload


def _prompt_field(spec: dict[str, Any]) -> Any:
    """Prompt for one field per its declared type."""
    ftype = spec.get("type", "text")
    label = spec.get("label") or spec.get("name", "value")
    required = spec.get("required", True)
    default = spec.get("default")

    if ftype == "hidden":
        return spec.get("value")

    if ftype == "bool":
        bool_default = bool(default) if default is not None else True
        return click.confirm(label, default=bool_default)

    if ftype == "int":
        prompt_kwargs: dict[str, Any] = {"type": int}
        if default is not None:
            prompt_kwargs["default"] = int(default)
        elif not required:
            prompt_kwargs["default"] = 0
        return click.prompt(label, **prompt_kwargs)

    if ftype in ("radio", "select"):
        options = spec.get("options") or []
        choices = [o.get("value") for o in options if isinstance(o, dict)]
        if not choices:
            return None
        for i, opt in enumerate(options, 1):
            click.echo(f"  {i}. {opt.get('label') or opt.get('value')}")
        idx = click.prompt(
            label,
            type=click.IntRange(1, len(choices)),
            default=1,
        )
        return choices[idx - 1]

    if ftype == "checkbox":
        options = spec.get("options") or []
        for i, opt in enumerate(options, 1):
            click.echo(f"  {i}. {opt.get('label') or opt.get('value')}")
        raw = click.prompt(f"{label} (comma-separated indices, e.g. 1,3)", default="")
        picked: list[str] = []
        for part in raw.split(","):
            part = part.strip()
            if not part.isdigit():
                continue
            i = int(part)
            if 1 <= i <= len(options):
                value = options[i - 1].get("value")
                if isinstance(value, str):
                    picked.append(value)
        return picked

    # text / textarea / unknown → freeform string
    prompt_kwargs = {"default": default if default is not None else ""}
    if not required:
        prompt_kwargs["show_default"] = False
    return click.prompt(label, **prompt_kwargs)


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
