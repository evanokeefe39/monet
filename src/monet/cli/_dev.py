"""monet dev — start the Aegra dev server with monet defaults."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Callable

from monet.cli._setup import check_env


@click.command()
@click.option(
    "--port",
    default=2026,
    type=int,
    help="Port for the Aegra dev server.",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True),
    help="Explicit path to an aegra.json. Overrides auto-detection.",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Show raw Aegra server output instead of monet's curated log.",
)
def dev(port: int, config_path: str | None, verbose: bool) -> None:
    """Start the Aegra dev server with monet's default graphs.

    Generates an Aegra config from monet's built-in graphs (entry,
    planning, execution) and worker/task routes.  If an ``aegra.json``
    or ``langgraph.json`` exists in the current directory, its graphs
    are merged on top of the defaults.

    Requires ``aegra-cli`` to be installed.
    """
    from monet.server._langgraph_config import (
        default_config,
        merge_config,
        write_config,
    )

    _print_logo()
    check_env()

    # Verify aegra CLI is available.
    if shutil.which("aegra") is None:
        raise click.ClickException(
            "aegra-cli not found.\nInstall with: pip install aegra-cli"
        )

    # Build merged config.
    if config_path is not None:
        # Explicit config — use as-is, no merging.
        resolved_config = Path(config_path)
    else:
        config = default_config()
        cwd = Path.cwd()

        # Merge with user's aegra.json or langgraph.json if present.
        user_config_path = cwd / "aegra.json"
        if not user_config_path.exists():
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

    cmd = ["aegra", "dev", "--config", str(resolved_config), "--port", str(port)]

    if verbose:
        click.echo(f"Starting dev server on port {port}... (verbose)")
        sys.exit(subprocess.call(cmd))

    click.echo(f"Starting dev server on port {port}...")
    sys.exit(_run_curated(cmd, port))


def _run_curated(cmd: list[str], port: int) -> int:
    """Run the Aegra subprocess and emit a simplified, monet-branded log.

    Readiness is detected by probing the server's ``/health`` endpoint.
    Stdout is still consumed so unexpected errors pass through to the user,
    but known-noisy log lines are dropped.
    """
    # Force utf-8 on the child process stdout so emojis in langgraph output
    # don't crash on Windows cp1252 consoles before we get a chance to filter.
    # PYTHONUNBUFFERED=1 ensures any pass-through error lines flush promptly.
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    ready_lock = threading.Lock()
    ready_state = {"printed": False}

    def _on_ready() -> None:
        with ready_lock:
            if ready_state["printed"]:
                return
            ready_state["printed"] = True
        _print_ready(port)

    probe = threading.Thread(
        target=_probe_ready,
        args=(port, proc, _on_ready),
        daemon=True,
    )
    probe.start()

    try:
        assert proc.stdout is not None
        # Use readline() in a loop rather than ``for line in proc.stdout``:
        # the file iterator does internal read-ahead buffering that bypasses
        # the pipe's line buffering, so lines arrive only after the buffer
        # fills. readline() honours line buffering correctly.
        while True:
            raw = proc.stdout.readline()
            if not raw:
                break
            line = raw.rstrip()
            if _should_drop(line):
                continue
            # Unknown line — pass through to the user.
            click.echo(line)
    except KeyboardInterrupt:
        click.echo("\nShutting down...")
        proc.terminate()

    return proc.wait()


def _probe_ready(
    port: int,
    proc: subprocess.Popen[str],
    on_ready: Callable[[], None],
    interval: float = 0.25,
) -> None:
    """Poll ``http://127.0.0.1:{port}/health`` until it 200s or the process exits.

    Runs in a daemon thread. Calls ``on_ready`` exactly once when the server
    responds successfully. Exits silently if the subprocess dies first.
    """
    url = f"http://127.0.0.1:{port}/health"
    while proc.poll() is None:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    on_ready()
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        time.sleep(interval)


# Substrings that identify lines monet should suppress in curated mode.
# Drop-list rather than keep-list so unknown errors surface by default.
_DROP_SUBSTRINGS: tuple[str, ...] = (
    "watchfiles",
    "changes detected",
    "langgraph_api",
    "langgraph_runtime",
    "aegra_api",
    "Welcome to",
    "This in-memory server",
    "For production use",
    "API Docs:",
    "Studio UI:",
    "API:",
    "Starting LangGraph dev server",
    "Starting Aegra",
    "Opening Studio",
    "INFO:",
)

# Box-drawing characters used in ASCII welcome banners. Lines made up
# entirely of these (plus whitespace) are banner art and get dropped.
_BANNER_CHARS = frozenset("╔╦╗║╚╩╝╠╣═╬┌┐└┘│─├┤┬┴┼")


def _should_drop(line: str) -> bool:
    """True if this langgraph stdout line should be hidden in curated mode."""
    stripped = line.strip()
    if not stripped:
        return True
    if any(sub in line for sub in _DROP_SUBSTRINGS):
        return True
    # ASCII banner lines: all non-space chars are box-drawing glyphs.
    return all(ch.isspace() or ch in _BANNER_CHARS for ch in stripped)


def _print_ready(port: int) -> None:
    """Print the monet-branded ready block."""
    base_url = f"http://127.0.0.1:{port}"
    click.echo()
    click.echo(f"  Ready on  {base_url}")
    click.echo(f"  API docs  {base_url}/docs")
    click.echo(f"  Health    {base_url}/health")
    click.echo()
    click.echo("  Press Ctrl-C to stop.")
    click.echo()


_LOGO = r"""
  _  _  _    __   _  _    _ _|_
 / |/ |/ |  /  \_/ |/ |  |/  |
   |  |  |_/\__/   |  |_/|__/|_/
"""


def _print_logo() -> None:
    """Print the monet CLI logo."""
    click.echo(_LOGO)
