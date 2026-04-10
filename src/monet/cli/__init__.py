"""monet CLI — command-line interface for the monet platform.

Commands:
    monet server   — start the orchestration server
    monet worker   — start a worker process
    monet register — register agent capabilities with a server
"""

from __future__ import annotations

import click

from monet.cli._register import register
from monet.cli._server import server
from monet.cli._worker import worker

__all__ = ["cli"]


@click.group()
@click.version_option(package_name="monet")
def cli() -> None:
    """monet — multi-agent orchestration platform."""


cli.add_command(worker)
cli.add_command(register)
cli.add_command(server)
