"""monet runs — manage and inspect orchestration runs."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from monet.client import MonetClient

from monet._constants import STANDARD_DEV_PORT
from monet.cli._render import (
    prompt_execution_decision,
    prompt_plan_decision,
    render_pending_table,
    render_run_table,
)
from monet.config import MONET_SERVER_URL


def _make_client(url: str) -> MonetClient:
    from monet.client import MonetClient

    return MonetClient(url)


@click.group()
def runs() -> None:
    """Manage and inspect orchestration runs."""


@runs.command(name="list")
@click.option(
    "--url",
    default=f"http://localhost:{STANDARD_DEV_PORT}",
    envvar=MONET_SERVER_URL,
    help="Aegra server URL.",
)
@click.option("--limit", default=20, help="Maximum runs to display.")
def list_runs(url: str, limit: int) -> None:
    """List recent runs with status, stages, and age."""
    asyncio.run(_list_runs(url, limit))


async def _list_runs(url: str, limit: int) -> None:
    client = _make_client(url)
    summaries = await client.list_runs(limit=limit)
    render_run_table(summaries)


@runs.command()
@click.option(
    "--url",
    default=f"http://localhost:{STANDARD_DEV_PORT}",
    envvar=MONET_SERVER_URL,
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
    default=f"http://localhost:{STANDARD_DEV_PORT}",
    envvar=MONET_SERVER_URL,
    help="Aegra server URL.",
)
def inspect(run_id: str, url: str) -> None:
    """Show full detail for a run: triage, plan, waves, artifacts."""
    asyncio.run(_inspect(url, run_id))


async def _inspect(url: str, run_id: str) -> None:
    from monet.pipelines.default import DefaultPipelineRunDetail
    from monet.pipelines.default.render import render_pipeline_run_detail

    client = _make_client(url)
    detail = await client.get_run(run_id)
    # If the run's stages look like the default pipeline, render the typed view.
    if any(s in detail.completed_stages for s in ("entry", "planning", "execution")):
        render_pipeline_run_detail(DefaultPipelineRunDetail.from_run_detail(detail))
    else:
        click.secho(f"Run {detail.run_id}", bold=True)
        click.echo(f"  Status: {detail.status}")
        if detail.completed_stages:
            click.echo(f"  Stages: {', '.join(detail.completed_stages)}")


@runs.command()
@click.argument("run_id")
@click.option(
    "--url",
    default=f"http://localhost:{STANDARD_DEV_PORT}",
    envvar=MONET_SERVER_URL,
    help="Aegra server URL.",
)
def resume(run_id: str, url: str) -> None:
    """Resume an interrupted run.

    Detects the pending interrupt's tag, prompts for a decision, and
    dispatches the matching HITL verb.
    """
    try:
        exit_code = asyncio.run(_resume(url, run_id))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(exit_code)


async def _resume(url: str, run_id: str) -> int:
    from monet.pipelines.default import (
        abort_run,
        approve_plan,
        reject_plan,
        retry_wave,
        revise_plan,
    )

    client = _make_client(url)
    detail = await client.get_run(run_id)

    if detail.status != "interrupted" or detail.pending_interrupt is None:
        click.echo(f"Run {run_id} is not interrupted (status: {detail.status}).")
        return 0

    tag = detail.pending_interrupt.tag

    if tag == "human_approval":
        decision = prompt_plan_decision()
        if decision == "approve":
            await approve_plan(client, run_id)
        elif decision == "revise":
            feedback = click.prompt("Feedback")
            await revise_plan(client, run_id, feedback)
        elif decision == "reject":
            await reject_plan(client, run_id)
            click.secho("Run rejected.", fg="red")
            return 1
        return 0

    if tag == "human_interrupt":
        exec_decision = prompt_execution_decision()
        if exec_decision == "retry":
            await retry_wave(client, run_id)
            return 0
        await abort_run(client, run_id)
        click.secho("Run aborted.", fg="red")
        return 1

    click.echo(f"Run {run_id} is interrupted at unknown tag: {tag}")
    return 1
