"""monet dev — start the LangGraph dev server with monet defaults."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import click

from monet.cli._setup import check_env


@click.command()
@click.option(
    "--port",
    default=2024,
    type=int,
    help="Port for the LangGraph dev server.",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True),
    help="Explicit path to a langgraph.json. Overrides auto-detection.",
)
def dev(port: int, config_path: str | None) -> None:
    """Start the LangGraph dev server with monet's default graphs.

    Generates a LangGraph config from monet's built-in graphs (entry,
    planning, execution). If a ``langgraph.json`` exists in the current
    directory, its graphs are merged on top of the defaults.

    Requires ``langgraph-cli`` to be installed.
    """
    from monet.server._langgraph_config import (
        default_config,
        merge_config,
        write_config,
    )

    _print_logo()
    check_env()

    # Verify langgraph CLI is available.
    if shutil.which("langgraph") is None:
        raise click.ClickException(
            "langgraph-cli not found.\nInstall with: pip install 'langgraph-cli[inmem]'"
        )

    # Build merged config.
    if config_path is not None:
        # Explicit config — use as-is, no merging.
        resolved_config = Path(config_path)
    else:
        config = default_config()
        cwd = Path.cwd()

        # Merge with user's langgraph.json if present.
        user_config_path = cwd / "langgraph.json"
        if user_config_path.exists():
            try:
                user_config = json.loads(user_config_path.read_text())
            except (json.JSONDecodeError, ValueError) as exc:
                raise click.ClickException(
                    f"Invalid JSON in {user_config_path}: {exc}"
                ) from exc
            config = merge_config(config, user_config)
            click.echo(
                f"Merged {len(user_config.get('graphs', {}))} user graph(s) "
                "with monet defaults."
            )

        resolved_config = write_config(config, cwd)

    click.echo(f"Starting LangGraph dev server on port {port}...")
    cmd = ["langgraph", "dev", "--config", str(resolved_config), "--port", str(port)]
    sys.exit(subprocess.call(cmd))


_LOGO = r"""
  _  _  _    __   _  _    _ _|_
 / |/ |/ |  /  \_/ |/ |  |/  |
   |  |  |_/\__/   |  |_/|__/|_/
"""


def _print_logo() -> None:
    """Print the monet CLI logo."""
    click.echo(_LOGO)
