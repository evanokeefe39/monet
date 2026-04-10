"""monet register — register agent capabilities with the orchestration server."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

import click
import httpx


@click.command()
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True),
    help="Directory to scan for agents.",
)
@click.option(
    "--server-url",
    envvar="MONET_SERVER_URL",
    required=True,
    help="Orchestration server URL.",
)
@click.option(
    "--api-key",
    envvar="MONET_API_KEY",
    required=True,
    help="API key for server auth.",
)
def register(path: str, server_url: str, api_key: str) -> None:
    """Register agent capabilities with the orchestration server.

    Scans --path for @agent decorated functions and registers their
    capabilities with the server, grouped by pool.
    """
    asyncio.run(_register(Path(path), server_url, api_key))


async def _register(path: Path, server_url: str, api_key: str) -> None:
    """Discover agents and register capabilities with the server."""
    from monet.cli._discovery import discover_agents

    discovered = discover_agents(path)
    if not discovered:
        click.echo("No agents found in " + str(path))
        return

    # Group by pool.
    by_pool: dict[str, list[dict[str, str]]] = defaultdict(list)
    for agent in discovered:
        by_pool[agent.pool].append(
            {
                "agent_id": agent.agent_id,
                "command": agent.command,
                "description": "",
                "pool": agent.pool,
            }
        )

    base_url = server_url.rstrip("/")
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    ) as client:
        for pool_name, capabilities in by_pool.items():
            resp = await client.post(
                f"{base_url}/api/v1/deployments",
                json={"pool": pool_name, "capabilities": capabilities},
            )
            resp.raise_for_status()

    total = len(discovered)
    pools = len(by_pool)
    click.echo(f"Registered {total} agents across {pools} pools:")
    for pool_name, capabilities in sorted(by_pool.items()):
        labels = [f"{c['agent_id']}/{c['command']}" for c in capabilities]
        click.echo(f"  {pool_name}: {', '.join(labels)}")
