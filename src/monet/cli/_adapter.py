"""monet adapter CLI — config-based adapter lifecycle commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import httpx

_INIT_TEMPLATES = {
    "openai": """\
name = "my-agent"
type = "openai"
url = "http://localhost:8080"
""",
    "http": """\
name = "my-agent"
type = "http"
url = "http://localhost:8080/chat"
health = "/health"

[request]
body.message = "$.payload.task"

[response]
output = "$.message"
""",
    "stdio": """\
name = "my-agent"
type = "stdio"

[stdio]
command = ["my-agent-bin", "acp"]
plugin = "my_plugin:run_task"
# init_rpc = "initialize"
""",
    "plugin": """\
name = "my-agent"
type = "plugin"
plugin = "my_module:handle_task"
""",
}


@click.group()
def adapter() -> None:
    """Config-based adapter lifecycle commands."""


@adapter.command("serve")
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", type=int, default=None, help="Override port from config.")
def adapter_serve(config_path: Path, host: str, port: int | None) -> None:
    """Start adapter server from CONFIG_PATH."""
    from monet.adapter._server import serve

    serve(config_path, host=host, port=port)


@adapter.command("check")
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
def adapter_check(config_path: Path) -> None:
    """Validate CONFIG_PATH and print a summary."""
    from monet.adapter._config import load_config

    try:
        config = load_config(config_path)
    except Exception as exc:
        click.echo(f"Invalid config: {exc}", err=True)
        sys.exit(1)

    click.echo(f"name:    {config.name}")
    click.echo(f"type:    {config.type}")
    if config.url:
        click.echo(f"url:     {config.url}")
    click.echo(f"port:    {config.port}")
    click.echo(f"timeout: {config.timeout}s")
    if config.process.command:
        click.echo(f"command: {' '.join(config.process.command)}")
    click.echo("Config is valid.")


@adapter.command("init")
@click.option(
    "--type",
    "adapter_type",
    default="openai",
    type=click.Choice(["openai", "http", "stdio", "plugin"]),
    show_default=True,
    help="Adapter type.",
)
def adapter_init(adapter_type: str) -> None:
    """Print a starter TOML config to stdout."""
    click.echo(_INIT_TEMPLATES[adapter_type], nl=False)


@adapter.command("ping")
@click.argument("url")
@click.option("--task", default="hello", show_default=True, help="Test task string.")
def adapter_ping(url: str, task: str) -> None:
    """Hit /health then POST /task against a running adapter at URL."""
    url = url.rstrip("/")

    try:
        r = httpx.get(f"{url}/health", timeout=5.0)
        health = r.json()
        status = "ok" if health.get("ok") else "degraded"
        click.echo(f"health: {status} ({r.status_code})")
    except Exception as exc:
        click.echo(f"health: unreachable — {exc}", err=True)
        sys.exit(1)

    try:
        payload = {"task_id": "ping-test", "payload": {"task": task}}
        r = httpx.post(f"{url}/task", json=payload, timeout=30.0)
        data = r.json()
        click.echo(f"task:   {r.status_code}")
        click.echo(json.dumps(data, indent=2))
    except Exception as exc:
        click.echo(f"task: failed — {exc}", err=True)
        sys.exit(1)
