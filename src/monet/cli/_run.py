"""monet run — run a topic against a monet server with hybrid output.

Output modes (controlled by --output, default auto):

- text: human-readable colored events to stdout
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
from monet.cli._render import render_event, serialize_event
from monet.cli._setup import check_env
from monet.config import MONET_SERVER_URL

_EXIT_SUCCESS = 0
_EXIT_AGENT_ERROR = 1
_EXIT_CONNECTION_ERROR = 2
_EXIT_USAGE_ERROR = 3


def _resolve_entrypoint(name: str | None) -> Entrypoint:
    """Look up the ``monet run`` entrypoint by name (or ``default``)."""
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

    With no ``--graph`` (or ``--graph default``), drives the default
    pipeline (entry → planning → execution) with HITL plan approval.

    With ``--graph <name>``, invokes that entrypoint's graph as a
    single-graph stream. Internal subgraphs like ``planning`` and
    ``execution`` are not declared entrypoints and cannot be invoked.

    When piped, defaults to NDJSON event stream on stdout.
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
    is_default = entrypoint_name is None or entrypoint_name == "default"

    try:
        if is_default:
            exit_code = asyncio.run(
                _default_pipeline_run(resolved_topic, url, auto_approve, mode)
            )
        else:
            exit_code = asyncio.run(
                _single_graph_run(resolved_topic, url, mode, ep["graph"])
            )
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
    """Drive one graph directly and stream typed core events."""
    from monet.client import MonetClient, RunComplete, RunFailed
    from monet.client._wire import task_input

    preflight_error = await _preflight_server(url)
    if preflight_error is not None:
        click.echo(preflight_error, err=True)
        return _EXIT_CONNECTION_ERROR

    client = MonetClient(url)
    run_id: str | None = None
    try:
        async for event in client.run(graph_id, task_input(topic, "")):
            if run_id is None and hasattr(event, "run_id"):
                run_id = event.run_id
                click.echo(f"Run {run_id}", err=True)
            if mode == "json":
                click.echo(serialize_event(event))
            else:
                render_event(event)
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
    """Probe *url*'s ``/health`` endpoint. Return an error string on failure."""
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


async def _default_pipeline_run(
    topic: str,
    url: str,
    auto_approve: bool,
    mode: str,
) -> int:
    """Drive the default pipeline with output mode routing and HITL."""
    from monet.cli._render import prompt_plan_decision
    from monet.client import MonetClient, RunComplete, RunFailed
    from monet.pipelines.default import (
        ExecutionInterrupt,
        PlanInterrupt,
        abort_run,
        approve_plan,
        reject_plan,
        retry_wave,
        revise_plan,
    )
    from monet.pipelines.default import (
        run as run_default_pipeline,
    )
    from monet.pipelines.default.render import render_pipeline_event

    preflight_error = await _preflight_server(url)
    if preflight_error is not None:
        click.echo(preflight_error, err=True)
        return _EXIT_CONNECTION_ERROR

    try:
        client = MonetClient(url)
    except (ConnectionError, OSError) as exc:
        click.echo(f"Connection error: {exc}", err=True)
        return _EXIT_CONNECTION_ERROR

    run_id: str | None = None

    def _emit(event: object) -> None:
        if mode == "json":
            click.echo(serialize_event(event))  # type: ignore[arg-type]
        else:
            if isinstance(event, RunComplete | RunFailed):
                render_event(event)
            else:
                render_pipeline_event(event)  # type: ignore[arg-type]

    try:
        async for event in run_default_pipeline(
            client, topic, auto_approve=auto_approve
        ):
            if run_id is None and hasattr(event, "run_id"):
                run_id = event.run_id
                click.echo(f"Run {run_id}", err=True)

            _emit(event)

            if isinstance(event, PlanInterrupt) and run_id and mode == "text":
                decision = prompt_plan_decision()

                if decision == "approve":
                    await approve_plan(client, run_id)
                    return await _resume_pipeline(client, run_id, mode)
                if decision == "revise":
                    feedback = click.prompt("Feedback")
                    await revise_plan(client, run_id, feedback)
                    return _EXIT_SUCCESS
                if decision == "reject":
                    await reject_plan(client, run_id)
                    click.secho("Run rejected.", fg="red")
                    return _EXIT_AGENT_ERROR

                return _EXIT_SUCCESS

            if isinstance(event, ExecutionInterrupt) and run_id:
                if mode == "json":
                    return _EXIT_SUCCESS
                return await _handle_execution_interrupt(
                    client, run_id, mode, abort_run, retry_wave
                )

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


async def _resume_pipeline(
    client: MonetClient,
    run_id: str,
    mode: str,
) -> int:
    """After approving a plan, observe the run to completion.

    ``approve_plan`` dispatched the resume; now poll the run's state
    until it completes or hits another interrupt. We don't re-stream
    planning — execution is a separate thread the adapter creates
    when planning finishes. For now we exit with success after plan
    approval; the user can use ``monet runs`` to observe execution.
    """
    # TODO(pipeline-runtime): after approve, the default-pipeline
    # adapter should observe plan completion server-side and auto-
    # launch execution. Until that's wired, we acknowledge and let
    # ``monet runs`` pick up the run.
    click.secho("Plan approved — execution launching.", fg="green")
    return _EXIT_SUCCESS


async def _handle_execution_interrupt(
    client: MonetClient,
    run_id: str,
    mode: str,
    abort_fn: object,
    retry_fn: object,
) -> int:
    """Prompt for retry/abort on an execution interrupt."""
    from monet.cli._render import prompt_execution_decision

    decision = prompt_execution_decision()

    if decision == "retry":
        await retry_fn(client, run_id)  # type: ignore[operator]
        return _EXIT_SUCCESS

    await abort_fn(client, run_id)  # type: ignore[operator]
    click.secho("Run aborted.", fg="red")
    return _EXIT_AGENT_ERROR
