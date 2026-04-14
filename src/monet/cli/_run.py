"""monet run — run a topic against a monet server with hybrid output.

Output modes (controlled by --output, default auto):
- text: human-readable colored events to stdout (current behavior)
- json: NDJSON event stream to stdout, errors to stderr
- auto: text if stdout is a TTY, json if piped
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from monet._graph_config import Entrypoint
    from monet.client import MonetClient

from monet._constants import STANDARD_DEV_PORT
from monet.cli._render import (
    prompt_execution_decision,
    prompt_plan_decision,
    render_event,
    serialize_event,
)
from monet.cli._setup import check_env

# Exit codes.
_EXIT_SUCCESS = 0
_EXIT_AGENT_ERROR = 1
_EXIT_CONNECTION_ERROR = 2
_EXIT_USAGE_ERROR = 3


def _resolve_entrypoint(name: str | None) -> Entrypoint:
    """Look up the ``monet run`` entrypoint by name (or 'default').

    Raises ``click.UsageError`` if ``name`` is supplied but not declared
    in ``monet.toml [entrypoints]``. Internal subgraphs like ``planning``
    and ``execution`` are deliberately un-invocable this way.
    """
    from monet._graph_config import load_entrypoints

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


def _resolve_graph_ids(entry_graph: str) -> dict[str, str]:
    """Build the role→graph_id mapping used by ``MonetClient`` for pipeline runs.

    Starts from ``load_graph_roles()`` and overrides the ``entry`` role
    with the entrypoint's configured graph.
    """
    from monet._graph_config import load_graph_roles

    ids = load_graph_roles()
    ids["entry"] = entry_graph
    return ids


def _resolve_output_mode(output: str) -> str:
    """Resolve ``auto`` to ``text`` or ``json`` based on TTY detection."""
    if output != "auto":
        return output
    return "text" if sys.stdout.isatty() else "json"


def _read_topic_from_stdin() -> str | None:
    """Read a topic from stdin when it is not a TTY.

    Returns ``None`` if stdin is a TTY or empty.
    """
    if sys.stdin.isatty():
        return None
    topic = sys.stdin.read().strip()
    return topic or None


@click.command()
@click.argument("topic", required=False)
@click.option(
    "--url",
    default=f"http://localhost:{STANDARD_DEV_PORT}",
    envvar="MONET_SERVER_URL",
    help="Aegra server URL.",
)
@click.option(
    "--auto-approve",
    is_flag=True,
    default=False,
    help="Auto-approve plans without prompting.",
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
    auto_approve: bool,
    output_mode: str,
    entrypoint_name: str | None,
) -> None:
    """Run a topic through a declared entrypoint.

    With no ``--graph``, drives the default pipeline
    (entry → planning → execution) with HITL plan approval.

    With ``--graph <name>``, looks up ``[entrypoints.<name>]`` in
    ``monet.toml`` and dispatches by its declared ``kind``. Internal
    subgraphs like ``planning`` and ``execution`` are intentionally
    not invocable this way.

    When piped, defaults to NDJSON event stream on stdout.
    Use --output text to force human-readable output.
    """
    check_env()
    mode = _resolve_output_mode(output_mode)

    # Resolve topic: positional arg > stdin > error.
    resolved_topic = topic
    if resolved_topic is None:
        resolved_topic = _read_topic_from_stdin()
    if resolved_topic is None:
        raise click.UsageError("Missing topic. Provide as argument or pipe via stdin.")

    # In json mode, force auto-approve (no interactive prompts).
    if mode == "json":
        auto_approve = True

    ep = _resolve_entrypoint(entrypoint_name)

    try:
        if ep["kind"] == "pipeline":
            graph_ids = _resolve_graph_ids(ep["graph"])
            exit_code = asyncio.run(
                _interactive_run(resolved_topic, url, auto_approve, mode, graph_ids)
            )
        elif ep["kind"] == "single":
            exit_code = asyncio.run(
                _single_graph_run(resolved_topic, url, mode, ep["graph"])
            )
        else:  # "messages"
            click.echo(
                f"Entrypoint kind '{ep['kind']}' is not driven from `monet run`. "
                "Use `monet chat` for chat-style graphs.",
                err=True,
            )
            raise SystemExit(_EXIT_USAGE_ERROR)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ConnectionError, OSError) as exc:
        click.echo(f"Connection error: {exc}", err=True)
        raise SystemExit(_EXIT_CONNECTION_ERROR) from exc

    raise SystemExit(exit_code)


async def _single_graph_run(
    topic: str,
    url: str,
    mode: str,
    graph_id: str,
) -> int:
    """Drive one graph directly with ``{task, run_id, trace_id}`` input.

    Streams the run via ``MonetClient.run_single`` and emits typed events
    (triage_complete, run_complete, run_failed) in text or NDJSON mode.
    """
    from monet.client import MonetClient

    preflight_error = await _preflight_server(url)
    if preflight_error is not None:
        click.echo(preflight_error, err=True)
        return _EXIT_CONNECTION_ERROR

    client = MonetClient(url)
    run_id: str | None = None
    try:
        async for event in client.run_single(graph_id, topic):
            if run_id is None and hasattr(event, "run_id"):
                run_id = event.run_id
                click.echo(f"Run {run_id}", err=True)
            if mode == "json":
                click.echo(serialize_event(event))
            else:
                render_event(event)
            from monet.client._events import RunComplete, RunFailed

            if isinstance(event, RunFailed):
                return _EXIT_AGENT_ERROR
            if isinstance(event, RunComplete):
                return _EXIT_SUCCESS
    except (ConnectionError, OSError) as exc:
        click.echo(f"Connection error: {exc}", err=True)
        return _EXIT_CONNECTION_ERROR
    except Exception as exc:
        click.echo(f"Unexpected error: {exc}", err=True)
        return _EXIT_AGENT_ERROR

    return _EXIT_SUCCESS


async def _preflight_server(url: str) -> str | None:
    """Probe ``url``'s /health endpoint. Return an error string on failure."""
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
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return f"Cannot reach monet server at {url}. Is `monet dev` running?"
    return None


async def _interactive_run(
    topic: str,
    url: str,
    auto_approve: bool,
    mode: str,
    graph_ids: dict[str, str] | None = None,
) -> int:
    """Run a topic with output mode routing and HITL handling.

    Returns an exit code: 0 success, 1 agent error, 2 connection error.
    """
    from monet.client import MonetClient
    from monet.client._events import (
        ExecutionInterrupt,
        PlanInterrupt,
        RunComplete,
        RunFailed,
    )

    preflight_error = await _preflight_server(url)
    if preflight_error is not None:
        click.echo(preflight_error, err=True)
        return _EXIT_CONNECTION_ERROR

    try:
        client = MonetClient(url, graph_ids=graph_ids)
    except (ConnectionError, OSError) as exc:
        click.echo(f"Connection error: {exc}", err=True)
        return _EXIT_CONNECTION_ERROR

    run_id: str | None = None

    try:
        async for event in client.run(topic, auto_approve=auto_approve):
            # Capture run_id from first event.
            if run_id is None and hasattr(event, "run_id"):
                run_id = event.run_id
                click.echo(f"Run {run_id}", err=True)

            # Emit event in the appropriate format.
            if mode == "json":
                click.echo(serialize_event(event))
            else:
                render_event(event)

            # HITL: plan approval (text mode only — json mode auto-approves).
            if isinstance(event, PlanInterrupt) and run_id and mode == "text":
                decision = prompt_plan_decision()

                if decision == "approve":
                    async for follow_up in client.approve_plan(run_id):
                        render_event(follow_up)
                        if isinstance(follow_up, ExecutionInterrupt):
                            return await _handle_execution_interrupt(
                                client, run_id, mode
                            )

                elif decision == "revise":
                    feedback = click.prompt("Feedback")
                    async for follow_up in client.revise_plan(run_id, feedback):
                        render_event(follow_up)

                elif decision == "reject":
                    await client.reject_plan(run_id)
                    click.secho("Run rejected.", fg="red")
                    return _EXIT_AGENT_ERROR

                return _EXIT_SUCCESS

            # HITL: execution interrupt.
            if isinstance(event, ExecutionInterrupt) and run_id:
                if mode == "json":
                    # Already emitted as NDJSON; exit for external resume.
                    return _EXIT_SUCCESS
                return await _handle_execution_interrupt(client, run_id, mode)

            if isinstance(event, RunFailed):
                return _EXIT_AGENT_ERROR

            if isinstance(event, RunComplete):
                return _EXIT_SUCCESS

    except (ConnectionError, OSError) as exc:
        click.echo(f"Connection error: {exc}", err=True)
        return _EXIT_CONNECTION_ERROR
    except Exception as exc:
        click.echo(f"Unexpected error: {exc}", err=True)
        return _EXIT_AGENT_ERROR

    return _EXIT_SUCCESS


async def _handle_execution_interrupt(
    client: MonetClient,
    run_id: str,
    mode: str,
) -> int:
    """Prompt for retry/abort on an execution interrupt.

    Returns an exit code.
    """
    from monet.client._events import RunFailed

    decision = prompt_execution_decision()

    if decision == "retry":
        async for event in client.retry_wave(run_id):
            if mode == "json":
                click.echo(serialize_event(event))
            else:
                render_event(event)
            if isinstance(event, RunFailed):
                return _EXIT_AGENT_ERROR
        return _EXIT_SUCCESS

    await client.abort_run(run_id)
    click.secho("Run aborted.", fg="red")
    return _EXIT_AGENT_ERROR
