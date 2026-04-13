"""monet runs — manage and inspect orchestration runs."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from monet.client import MonetClient

from monet.cli._render import (
    prompt_execution_decision,
    prompt_plan_decision,
    render_event,
    render_pending_table,
    render_run_detail,
    render_run_table,
)


def _make_client(url: str) -> MonetClient:
    from monet.client import MonetClient

    return MonetClient(url)


@click.group()
def runs() -> None:
    """Manage and inspect orchestration runs."""


@runs.command(name="list")
@click.option(
    "--url",
    default="http://localhost:2026",
    envvar="MONET_SERVER_URL",
    help="Aegra server URL.",
)
@click.option("--limit", default=20, help="Maximum runs to display.")
def list_runs(url: str, limit: int) -> None:
    """List recent runs with status, phase, and age."""
    asyncio.run(_list_runs(url, limit))


async def _list_runs(url: str, limit: int) -> None:
    client = _make_client(url)
    summaries = await client.list_runs(limit=limit)
    render_run_table(summaries)


@runs.command()
@click.option(
    "--url",
    default="http://localhost:2026",
    envvar="MONET_SERVER_URL",
    help="Aegra server URL.",
)
def pending(url: str) -> None:
    """Show runs awaiting human decisions."""
    asyncio.run(_pending(url))


async def _pending(url: str) -> None:
    client = _make_client(url)
    decisions = await client.list_pending()
    render_pending_table(decisions)


@runs.command()
@click.argument("run_id")
@click.option(
    "--url",
    default="http://localhost:2026",
    envvar="MONET_SERVER_URL",
    help="Aegra server URL.",
)
def inspect(run_id: str, url: str) -> None:
    """Show full detail for a run: triage, plan, waves, artifacts."""
    asyncio.run(_inspect(url, run_id))


async def _inspect(url: str, run_id: str) -> None:
    client = _make_client(url)
    detail = await client.get_run(run_id)
    render_run_detail(detail)


@runs.command()
@click.argument("run_id")
@click.option(
    "--url",
    default="http://localhost:2026",
    envvar="MONET_SERVER_URL",
    help="Aegra server URL.",
)
def resume(run_id: str, url: str) -> None:
    """Resume an interrupted run.

    Detects whether the run is paused at plan approval or execution
    interrupt, prompts for a decision, and streams remaining events.
    """
    try:
        exit_code = asyncio.run(_resume(url, run_id))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(exit_code)


async def _resume(url: str, run_id: str) -> int:
    from monet.client._events import (
        ExecutionInterrupt,
        RunFailed,
    )

    client = _make_client(url)
    detail = await client.get_run(run_id)

    if detail.status != "interrupted":
        click.echo(f"Run {run_id} is not interrupted (status: {detail.status}).")
        return 0

    # Determine interrupt type by checking which phase has a pending node.
    if detail.phase == "planning":
        decision = prompt_plan_decision()

        if decision == "approve":
            async for event in client.approve_plan(run_id):
                render_event(event)
                if isinstance(event, ExecutionInterrupt):
                    return await _handle_resume_exec(client, run_id)
                if isinstance(event, RunFailed):
                    return 1

        elif decision == "revise":
            feedback = click.prompt("Feedback")
            async for event in client.revise_plan(run_id, feedback):
                render_event(event)

        elif decision == "reject":
            await client.reject_plan(run_id)
            click.secho("Run rejected.", fg="red")
            return 1

    elif detail.phase == "execution":
        return await _handle_resume_exec(client, run_id)

    else:
        click.echo(f"Run {run_id} is interrupted in unexpected phase: {detail.phase}")
        return 1

    return 0


async def _handle_resume_exec(client: MonetClient, run_id: str) -> int:
    """Handle execution interrupt resume with prompt."""
    from monet.client._events import RunFailed

    decision = prompt_execution_decision()

    if decision == "retry":
        async for event in client.retry_wave(run_id):
            render_event(event)
            if isinstance(event, RunFailed):
                return 1
        return 0

    await client.abort_run(run_id)
    click.secho("Run aborted.", fg="red")
    return 1
