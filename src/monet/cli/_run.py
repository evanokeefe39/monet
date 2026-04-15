"""monet run — drive any declared entrypoint against a monet server.

Output modes (controlled by --output, default auto):

- text: human-readable colored events to stdout
- json: NDJSON event stream to stdout, errors to stderr
- auto: text if stdout is a TTY, json if piped

Every entrypoint is a single graph (post-Track-B collapse). HITL is
LangGraph-native: when a graph hits ``interrupt(...)``, the client
yields a generic :class:`monet.client.Interrupt`; the CLI renders the
form-schema envelope (or a raw-JSON fallback) and resumes via
``client.resume(run_id, tag, payload)``. There is no pipeline-specific
event projection in this module.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from monet.config import Entrypoint

from monet._ports import STANDARD_DEV_PORT
from monet.cli._render import (
    render_event,
    render_interrupt_form,
    serialize_event,
)
from monet.cli._setup import check_env
from monet.config import MONET_API_KEY, MONET_SERVER_URL

_EXIT_SUCCESS = 0
_EXIT_AGENT_ERROR = 1
_EXIT_CONNECTION_ERROR = 2


def _resolve_entrypoint(name: str | None) -> Entrypoint:
    """Look up the ``monet run`` entrypoint by name (or ``default``)."""
    from monet.config import load_entrypoints

    entrypoints = load_entrypoints()
    key = name or "default"
    ep = entrypoints.get(key)
    if ep is None:
        declared = ", ".join(sorted(entrypoints)) or "(none)"
        msg = (
            f"'{key}' is not a declared entrypoint. "
            f"Add it to [entrypoints] in monet.toml. "
            f"Declared: {declared}"
        )
        raise click.UsageError(msg)
    return ep


def _resolve_output_mode(output: str) -> str:
    """Resolve ``auto`` to ``text`` or ``json`` based on TTY detection."""
    if output != "auto":
        return output
    return "text" if sys.stdout.isatty() else "json"


def _read_topic_from_stdin() -> str | None:
    """Read a topic from stdin when it is not a TTY."""
    if sys.stdin.isatty():
        return None
    topic = sys.stdin.read().strip()
    return topic or None


@click.command()
@click.argument("topic", required=False)
@click.option(
    "--url",
    default=f"http://localhost:{STANDARD_DEV_PORT}",
    envvar=MONET_SERVER_URL,
    help="Aegra server URL.",
)
@click.option(
    "--api-key",
    envvar=MONET_API_KEY,
    default=None,
    help="API key for server auth.",
)
@click.option(
    "--auto-approve",
    is_flag=True,
    default=False,
    help="Auto-approve form-schema interrupts (picks the first 'approve'-like choice).",
)
@click.option(
    "--output",
    "output_mode",
    type=click.Choice(["auto", "text", "json"], case_sensitive=False),
    default="auto",
    help="Output format: auto (TTY=text, pipe=json), text, or json.",
)
@click.option(
    "--graph",
    "entrypoint_name",
    default=None,
    help=(
        "Name of a declared entrypoint in monet.toml (e.g. 'review'). "
        "Omit to use the default pipeline."
    ),
)
def run(
    topic: str | None,
    url: str,
    api_key: str | None,
    auto_approve: bool,
    output_mode: str,
    entrypoint_name: str | None,
) -> None:
    """Run a topic through a declared entrypoint.

    Drives the entrypoint's graph as a single stream and renders events
    to stdout. When the graph pauses on ``interrupt()``, prompts for the
    resume payload using the form-schema convention. When piped,
    defaults to NDJSON event stream on stdout.
    """
    check_env()
    mode = _resolve_output_mode(output_mode)

    resolved_topic = topic
    if resolved_topic is None:
        resolved_topic = _read_topic_from_stdin()
    if resolved_topic is None:
        raise click.UsageError("Missing topic. Provide as argument or pipe via stdin.")

    if mode == "json":
        auto_approve = True

    ep = _resolve_entrypoint(entrypoint_name)

    try:
        exit_code = asyncio.run(
            _drive_entrypoint(
                resolved_topic, url, api_key, mode, ep["graph"], auto_approve
            )
        )
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ConnectionError, OSError) as exc:
        click.echo(f"Connection error: {exc}", err=True)
        raise SystemExit(_EXIT_CONNECTION_ERROR) from exc

    raise SystemExit(exit_code)


async def _drive_entrypoint(
    topic: str,
    url: str,
    api_key: str | None,
    mode: str,
    graph_id: str,
    auto_approve: bool,
) -> int:
    """Stream a graph to completion, handling interrupts via form schema."""
    from monet.client import Interrupt, MonetClient, RunComplete, RunFailed
    from monet.client._wire import task_input

    preflight_error = await _preflight_server(url, api_key)
    if preflight_error is not None:
        click.echo(preflight_error, err=True)
        return _EXIT_CONNECTION_ERROR

    client = MonetClient(url, api_key=api_key)
    run_id: str | None = None

    def _emit(event: object) -> None:
        if mode == "json":
            click.echo(serialize_event(event))
        else:
            render_event(event)  # type: ignore[arg-type]

    initial_input: dict[str, object] | None = task_input(topic, "")
    while True:
        try:
            async for event in client.run(graph_id, initial_input):
                if run_id is None and hasattr(event, "run_id"):
                    run_id = event.run_id
                    click.echo(f"Run {run_id}", err=True)
                _emit(event)

                if isinstance(event, Interrupt) and run_id:
                    if mode == "json":
                        return _EXIT_SUCCESS
                    payload = _resolve_interrupt_payload(event, auto_approve)
                    if payload is None:
                        click.secho("Run aborted by user.", fg="red", err=True)
                        return _EXIT_AGENT_ERROR
                    await client.resume(run_id, event.tag, payload)
                    # Loop back to keep streaming after resume.
                    initial_input = None
                    break

                if isinstance(event, RunFailed):
                    return _EXIT_AGENT_ERROR
                if isinstance(event, RunComplete):
                    return _EXIT_SUCCESS
            else:
                # Stream ended without a terminal event or interrupt.
                return _EXIT_SUCCESS
        except (ConnectionError, OSError) as exc:
            click.echo(f"Connection error: {exc}", err=True)
            return _EXIT_CONNECTION_ERROR
        except Exception as exc:
            click.echo(f"Unexpected error: {exc}", err=True)
            return _EXIT_AGENT_ERROR


def _resolve_interrupt_payload(
    event: object,
    auto_approve: bool,
) -> dict[str, object] | None:
    """Build a resume payload for an Interrupt event.

    Returns None to signal user abort. With ``auto_approve``, picks the
    first option whose ``id`` looks like an approval (``approve``,
    ``ok``, ``yes``, ``retry``, or the first option as fallback).
    Without auto-approve, prompts via :func:`render_interrupt_form`.
    """
    values = getattr(event, "values", {}) or {}
    if auto_approve:
        return _auto_approve_payload(values)
    return render_interrupt_form(values)


def _auto_approve_payload(values: dict[str, object]) -> dict[str, object]:
    """Pick a sensible auto-approve payload from a form-schema envelope."""
    payload: dict[str, object] = {}
    if not isinstance(values, dict):
        return payload
    fields = values.get("fields") or []
    if not isinstance(fields, list):
        return payload
    for field_spec in fields:
        if not isinstance(field_spec, dict):
            continue
        name = field_spec.get("name")
        if not isinstance(name, str):
            continue
        ftype = field_spec.get("type")
        if ftype in ("radio", "select"):
            options = field_spec.get("options") or []
            if not isinstance(options, list) or not options:
                continue
            picked = _pick_approval_option(options)
            if picked is not None:
                payload[name] = picked
        # Other field types are skipped — auto-approve answers the
        # primary action only and leaves optional fields blank.
    return payload


def _pick_approval_option(options: list[object]) -> object:
    """Choose the option that looks most like an approval."""
    approval_aliases = ("approve", "ok", "yes", "retry", "accept", "continue")
    for opt in options:
        if isinstance(opt, dict):
            value = opt.get("value")
            if isinstance(value, str) and value.lower() in approval_aliases:
                return value
    # Fall back to the first option's value.
    first = options[0]
    if isinstance(first, dict):
        return first.get("value")
    return first


async def _preflight_server(url: str, api_key: str | None = None) -> str | None:
    """Probe *url*'s ``/health`` endpoint. Return an error string on failure.

    When *api_key* is provided, also probe an authenticated endpoint so a
    misconfigured key surfaces a clear message instead of an opaque
    ``langgraph_sdk`` failure mid-stream.
    """
    import httpx

    base = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=2.0) as probe:
            resp = await probe.get(f"{base}/health")
            if resp.status_code != 200:
                return (
                    f"Cannot reach monet server at {url} "
                    f"(health returned {resp.status_code}). "
                    "Is `monet dev` running?"
                )
            if api_key:
                headers = {"Authorization": f"Bearer {api_key}"}
                auth_resp = await probe.get(
                    f"{base}/api/v1/deployments", headers=headers
                )
                if auth_resp.status_code in (401, 403):
                    return (
                        f"Authentication failed against {url} "
                        f"({auth_resp.status_code}). "
                        "Check MONET_API_KEY (or pass --api-key)."
                    )
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return f"Cannot reach monet server at {url}. Is `monet dev` running?"
    return None
