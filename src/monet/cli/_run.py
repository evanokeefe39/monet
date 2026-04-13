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
    from monet.client import MonetClient

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


def _resolve_graph_ids(graph_override: str | None, command: str) -> dict[str, str]:
    """Resolve graph IDs from monet.toml + env vars + CLI flag."""
    from monet._graph_config import load_graph_roles

    ids = load_graph_roles()

    if graph_override:
        if command == "run":
            ids["entry"] = graph_override
        elif command == "chat":
            ids["chat"] = graph_override

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
    default="http://localhost:2026",
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
    "graph_override",
    default=None,
    help="Target a specific graph ID instead of the default pipeline.",
)
def run(
    topic: str | None,
    url: str,
    auto_approve: bool,
    output_mode: str,
    graph_override: str | None,
) -> None:
    """Run a topic through the monet orchestration pipeline.

    Connects to a running Aegra server, streams events, and handles
    human decisions at plan approval and execution interrupt points.

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

    # Resolve graph IDs from config + CLI override.
    graph_ids = _resolve_graph_ids(graph_override, "run")

    try:
        exit_code = asyncio.run(
            _interactive_run(resolved_topic, url, auto_approve, mode, graph_ids)
        )
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ConnectionError, OSError) as exc:
        click.echo(f"Connection error: {exc}", err=True)
        raise SystemExit(_EXIT_CONNECTION_ERROR) from exc

    raise SystemExit(exit_code)


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
