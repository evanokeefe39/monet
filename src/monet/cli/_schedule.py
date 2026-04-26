"""monet schedule — manage recurring graph runs."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import click
import httpx

from monet._ports import STANDARD_DEV_PORT
from monet.config import MONET_API_KEY, MONET_SERVER_URL

_URL_DEFAULT = f"http://localhost:{STANDARD_DEV_PORT}"


def _headers(api_key: str | None) -> dict[str, str]:
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def _base(url: str) -> str:
    return url.rstrip("/") + "/api/v1"


@click.group()
def schedule() -> None:
    """Manage recurring scheduled graph runs."""


@schedule.command(name="create")
@click.argument("graph_id")
@click.argument("cron_expression")
@click.option(
    "--input",
    "input_json",
    default="{}",
    help="JSON input dict for the graph.",
)
@click.option("--url", default=_URL_DEFAULT, envvar=MONET_SERVER_URL)
@click.option("--api-key", envvar=MONET_API_KEY, default=None)
def create(
    graph_id: str,
    cron_expression: str,
    input_json: str,
    url: str,
    api_key: str | None,
) -> None:
    """Create a schedule: GRAPH_ID CRON_EXPRESSION."""
    try:
        input_data: dict[str, Any] = json.loads(input_json)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid --input JSON: {exc}") from exc

    async def _run() -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_base(url)}/schedules",
                json={
                    "graph_id": graph_id,
                    "cron_expression": cron_expression,
                    "input": input_data,
                },
                headers=_headers(api_key),
            )
            resp.raise_for_status()
            data = resp.json()
            sid = data["schedule_id"]
            click.echo(f"Created schedule {sid} for {graph_id!r} [{cron_expression}]")

    asyncio.run(_run())


@schedule.command(name="list")
@click.option("--url", default=_URL_DEFAULT, envvar=MONET_SERVER_URL)
@click.option("--api-key", envvar=MONET_API_KEY, default=None)
def list_schedules(url: str, api_key: str | None) -> None:
    """List all schedules."""

    async def _run() -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_base(url)}/schedules",
                headers=_headers(api_key),
            )
            resp.raise_for_status()
            records = resp.json()
        if not records:
            click.echo("No schedules.")
            return
        for r in records:
            status = "enabled" if r["enabled"] else "disabled"
            last = r.get("last_run_at") or "never"
            cron = r["cron_expression"]
            click.echo(
                f"{r['schedule_id']}  {r['graph_id']}  [{cron}]  {status}  last={last}"
            )

    asyncio.run(_run())


@schedule.command(name="delete")
@click.argument("schedule_id")
@click.option("--url", default=_URL_DEFAULT, envvar=MONET_SERVER_URL)
@click.option("--api-key", envvar=MONET_API_KEY, default=None)
def delete(schedule_id: str, url: str, api_key: str | None) -> None:
    """Delete a schedule by ID."""

    async def _run() -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{_base(url)}/schedules/{schedule_id}",
                headers=_headers(api_key),
            )
            if resp.status_code == 404:
                raise click.ClickException(f"Schedule {schedule_id!r} not found.")
            resp.raise_for_status()
        click.echo(f"Deleted {schedule_id}")

    asyncio.run(_run())


@schedule.command(name="enable")
@click.argument("schedule_id")
@click.option("--url", default=_URL_DEFAULT, envvar=MONET_SERVER_URL)
@click.option("--api-key", envvar=MONET_API_KEY, default=None)
def enable(schedule_id: str, url: str, api_key: str | None) -> None:
    """Enable a schedule."""

    async def _run() -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_base(url)}/schedules/{schedule_id}/enable",
                headers=_headers(api_key),
            )
            if resp.status_code == 404:
                raise click.ClickException(f"Schedule {schedule_id!r} not found.")
            resp.raise_for_status()
        click.echo(f"Enabled {schedule_id}")

    asyncio.run(_run())


@schedule.command(name="disable")
@click.argument("schedule_id")
@click.option("--url", default=_URL_DEFAULT, envvar=MONET_SERVER_URL)
@click.option("--api-key", envvar=MONET_API_KEY, default=None)
def disable(schedule_id: str, url: str, api_key: str | None) -> None:
    """Disable a schedule."""

    async def _run() -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_base(url)}/schedules/{schedule_id}/disable",
                headers=_headers(api_key),
            )
            if resp.status_code == 404:
                raise click.ClickException(f"Schedule {schedule_id!r} not found.")
            resp.raise_for_status()
        click.echo(f"Disabled {schedule_id}")

    asyncio.run(_run())
