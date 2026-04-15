"""monet CLI — command-line interface for the monet platform.

Commands:
    monet run      — run a topic (pipe-friendly, NDJSON or text output)
    monet runs     — list, inspect, resume orchestration runs
    monet chat     — interactive multi-turn conversation REPL
    monet server   — start the orchestration server
    monet worker   — start a worker process
    monet register — register agent capabilities with a server
    monet db       — artifact index schema migration commands
"""

from __future__ import annotations

import click

from monet.cli._chat import chat
from monet.cli._db import db
from monet.cli._dev import dev
from monet.cli._register import register
from monet.cli._run import run
from monet.cli._runs import runs
from monet.cli._server import server
from monet.cli._status import status
from monet.cli._worker import worker

__all__ = ["cli"]


@click.group()
@click.version_option(package_name="monet")
def cli() -> None:
    """monet — multi-agent orchestration platform."""


cli.add_command(chat)
cli.add_command(db)
cli.add_command(dev)
cli.add_command(run)
cli.add_command(runs)
cli.add_command(worker)
cli.add_command(register)
cli.add_command(server)
cli.add_command(status)
