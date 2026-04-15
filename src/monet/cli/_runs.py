"""monet runs — manage and inspect orchestration runs."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from monet.client import MonetClient

from monet._ports import STANDARD_DEV_PORT
from monet.cli._render import (
    render_interrupt_form,
    render_pending_table,
    render_run_table,
)
from monet.config import MONET_API_KEY, MONET_SERVER_URL


def _make_client(url: str, api_key: str | None) -> MonetClient:
    from monet.client import MonetClient

    return MonetClient(url, api_key=api_key)


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
@click.option(
    "--api-key",
    envvar=MONET_API_KEY,
    default=None,
    help="API key for server auth.",
)
@click.option("--limit", default=20, help="Maximum runs to display.")
def list_runs(url: str, api_key: str | None, limit: int) -> None:
    """List recent runs with status, stages, and age."""
    asyncio.run(_list_runs(url, api_key, limit))


async def _list_runs(url: str, api_key: str | None, limit: int) -> None:
    client = _make_client(url, api_key)
    summaries = await client.list_runs(limit=limit)
    render_run_table(summaries)


@runs.command()
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
def pending(url: str, api_key: str | None) -> None:
    """Show runs awaiting human decisions."""
    asyncio.run(_pending(url, api_key))


async def _pending(url: str, api_key: str | None) -> None:
    client = _make_client(url, api_key)
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
@click.option(
    "--api-key",
    envvar=MONET_API_KEY,
    default=None,
    help="API key for server auth.",
)
def inspect(run_id: str, url: str, api_key: str | None) -> None:
    """Show full detail for a run: status, completed stages, raw values."""
    asyncio.run(_inspect(url, api_key, run_id))


async def _inspect(url: str, api_key: str | None, run_id: str) -> None:
    client = _make_client(url, api_key)
    detail = await client.get_run(run_id)
    click.secho(f"Run {detail.run_id}", bold=True)
    click.echo(f"  Status: {detail.status}")
    if detail.completed_stages:
        click.echo(f"  Stages: {', '.join(detail.completed_stages)}")
    if detail.values:
        click.echo("  Values:")
        click.echo(json.dumps(detail.values, indent=2, default=str, ensure_ascii=False))
    if detail.pending_interrupt is not None:
        click.secho(f"  Paused at: {detail.pending_interrupt.tag}", fg="yellow")


@runs.command()
@click.argument("run_id")
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
def resume(run_id: str, url: str, api_key: str | None) -> None:
    """Resume an interrupted run.

    Renders the pending interrupt's form-schema envelope, collects a
    payload, and dispatches it via ``client.resume``. Falls back to a
    raw-JSON prompt if the interrupt doesn't follow the form-schema
    convention.
    """
    try:
        exit_code = asyncio.run(_resume(url, api_key, run_id))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(exit_code)


async def _resume(url: str, api_key: str | None, run_id: str) -> int:
    client = _make_client(url, api_key)
    detail = await client.get_run(run_id)

    if detail.status != "interrupted" or detail.pending_interrupt is None:
        click.echo(f"Run {run_id} is not interrupted (status: {detail.status}).")
        return 0

    pending_interrupt = detail.pending_interrupt
    payload = render_interrupt_form(pending_interrupt.values)
    await client.resume(run_id, pending_interrupt.tag, payload)
    return 0
