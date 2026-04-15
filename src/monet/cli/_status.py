"""monet status — show live workers and their capabilities."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import click

from monet._ports import STANDARD_DEV_PORT
from monet.config import MONET_API_KEY, MONET_SERVER_URL


def _parse_capabilities(caps: Any) -> list[dict[str, str]]:
    """Normalize capabilities from a deployment record.

    Handles both pre-parsed lists and JSON-encoded strings from the API.
    Returns an empty list on malformed data rather than crashing.
    """
    if isinstance(caps, list):
        return caps
    if isinstance(caps, str):
        try:
            parsed = json.loads(caps)
        except (json.JSONDecodeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _render_header(health: dict[str, Any]) -> None:
    """Render the common server/workers/queued status header."""
    click.secho(
        f"Server: {health.get('status', 'unknown')}  "
        f"Workers: {health.get('workers', 0)}  "
        f"Queued: {health.get('queued', 0)}",
        bold=True,
    )


@click.command()
@click.option(
    "--url",
    default=f"http://localhost:{STANDARD_DEV_PORT}",
    envvar=MONET_SERVER_URL,
    help="Orchestration server URL.",
)
@click.option(
    "--api-key",
    envvar=MONET_API_KEY,
    default=None,
    help="API key for server auth.",
)
@click.option(
    "--pool",
    default=None,
    help="Filter by pool name.",
)
@click.option(
    "--flat",
    is_flag=True,
    default=False,
    help="Output as a flat table (one row per capability).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output as JSON (pipe-friendly).",
)
def status(
    url: str, api_key: str | None, pool: str | None, flat: bool, as_json: bool
) -> None:
    """Show live workers and their agent capabilities."""
    asyncio.run(_show_status(url, api_key, pool, flat, as_json))


async def _show_status(
    url: str, api_key: str | None, pool: str | None, flat: bool, as_json: bool
) -> None:
    """Fetch deployments and health, render to terminal."""
    import httpx

    base = url.rstrip("/")
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
        # Health is unauthenticated.
        try:
            health_resp = await client.get(f"{base}/api/v1/health")
            health_resp.raise_for_status()
            health = health_resp.json()
        except (httpx.HTTPError, OSError) as exc:
            raise click.ClickException(f"Cannot reach server at {base}: {exc}") from exc

        # Deployments require auth.
        params: dict[str, str] = {}
        if pool:
            params["pool"] = pool
        try:
            dep_resp = await client.get(f"{base}/api/v1/deployments", params=params)
            dep_resp.raise_for_status()
            deployments: list[dict[str, Any]] = dep_resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise click.ClickException(
                    "Authentication required. Set MONET_API_KEY or --api-key."
                ) from exc
            raise

    if as_json:
        _render_json(health, deployments)
    elif flat:
        _render_flat(health, deployments)
    else:
        _render(health, deployments)


def _render(health: dict[str, Any], deployments: list[dict[str, Any]]) -> None:
    """Render status to terminal."""
    _render_header(health)

    if not deployments:
        click.echo("\nNo active deployments.")
        return

    click.echo()
    for dep in deployments:
        worker_id = dep.get("worker_id") or "unassigned"
        pool_name = dep.get("pool", "?")
        heartbeat = dep.get("last_heartbeat") or "never"

        click.secho(f"Worker {worker_id}", fg="cyan", bold=True)
        click.echo(f"  Pool: {pool_name}  Heartbeat: {heartbeat}")

        caps = _parse_capabilities(dep.get("capabilities") or [])

        if caps:
            click.echo("  Capabilities:")
            for cap in caps:
                agent_id = cap.get("agent_id", "?")
                command = cap.get("command", "?")
                desc = cap.get("description", "")
                line = f"    {agent_id}/{command}"
                if desc:
                    line += f" — {desc}"
                click.echo(line)
        else:
            click.echo("  Capabilities: none")


def _render_flat(health: dict[str, Any], deployments: list[dict[str, Any]]) -> None:
    """Render status as a flat table — one row per capability."""
    _render_header(health)

    if not deployments:
        click.echo("\nNo active deployments.")
        return

    # Collect all rows.
    rows: list[tuple[str, str, str, str, str]] = []
    for dep in deployments:
        worker_id = dep.get("worker_id") or "unassigned"
        pool_name = dep.get("pool", "?")
        caps = _parse_capabilities(dep.get("capabilities") or [])
        heartbeat = dep.get("last_heartbeat") or "never"

        if not caps:
            rows.append((worker_id, pool_name, "-", "-", heartbeat))
        else:
            for cap in caps:
                rows.append(
                    (
                        worker_id,
                        pool_name,
                        cap.get("agent_id", "?"),
                        cap.get("command", "?"),
                        heartbeat,
                    )
                )

    if not rows:
        click.echo("\nNo capabilities registered.")
        return

    # Column widths.
    headers = ("WORKER", "POOL", "AGENT", "COMMAND", "HEARTBEAT")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    click.echo()
    click.secho(fmt.format(*headers), dim=True)
    for row in rows:
        click.echo(fmt.format(*row))


def _render_json(health: dict[str, Any], deployments: list[dict[str, Any]]) -> None:
    """Render status as JSON to stdout. No color, no decoration."""
    # Normalize capabilities from JSON strings to lists.
    normalized: list[dict[str, Any]] = []
    for dep in deployments:
        entry = dict(dep)
        entry["capabilities"] = _parse_capabilities(entry.get("capabilities") or [])
        normalized.append(entry)

    output = {"health": health, "deployments": normalized}
    click.echo(json.dumps(output, indent=2))
