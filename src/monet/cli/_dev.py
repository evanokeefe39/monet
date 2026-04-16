"""monet dev — start the Aegra dev server with monet defaults."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import socket
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
    from typing import Any

from monet._ports import (
    STANDARD_DEV_PORT,
    STANDARD_POSTGRES_PORT,
    STANDARD_REDIS_PORT,
    state_file,
)
from monet.cli._setup import check_env


@click.group(invoke_without_command=True)
@click.option(
    "--port",
    default=STANDARD_DEV_PORT,
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
@click.option(
    "--clean",
    is_flag=True,
    default=False,
    help=(
        "Wipe this example's Postgres/Redis volumes before starting. "
        "Use to drop checkpoints, threads, and runs from prior sessions."
    ),
)
@click.pass_context
def dev(
    ctx: click.Context,
    port: int,
    config_path: str | None,
    verbose: bool,
    clean: bool,
) -> None:
    """Start the Aegra dev server with monet's default graphs.

    Generates an Aegra config from monet's built-in graphs (entry,
    planning, execution) and worker/task routes.  If an ``aegra.json``
    or ``langgraph.json`` exists in the current directory, its graphs
    are merged on top of the defaults.

    Before starting, any previously running example's docker stack is
    torn down (volumes preserved), so the standard ports are free.

    Requires ``aegra-cli`` to be installed.
    """
    if ctx.invoked_subcommand is not None:
        return

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

    # Port preflight — a second ``monet dev`` on the standard ports
    # would silently fail partway through (Aegra boots but Postgres
    # bind conflicts, or vice versa). Fail loudly now so the user
    # knows to stop the other instance.
    _check_ports_free(port)

    # Build merged config.
    cwd = Path.cwd()
    if config_path is not None:
        # Explicit config — use as-is, no merging.
        resolved_config = Path(config_path)
    else:
        config = default_config()

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

    # Before aegra starts the current example's stack, tear down any
    # previously-active example stack so the standard ports (5432, 6379)
    # are free. Only one example may run at a time.
    current_compose = _current_compose_path(resolved_config)
    if current_compose is not None:
        _teardown_previous(current_compose)
        _record_active_example(current_compose)

    # --clean removes this example's Postgres/Redis volumes so the
    # next boot starts with a fresh checkpoint store. Stop the
    # current example's containers first so the volumes detach.
    if clean:
        if current_compose is not None and current_compose.exists():
            _stop_compose_containers(current_compose)
        _wipe_example_volumes(cwd.name)

    cmd = ["aegra", "dev", "--config", str(resolved_config), "--port", str(port)]

    # Every example's compose lives at ``<example>/.monet/docker-compose.yml``,
    # so Docker defaults the project name to ``.monet`` → ``monet`` for every
    # one of them. That collapses all examples onto the same
    # ``monet_postgres_data`` volume and they collide on first launch after
    # a switch. Pin the project name to the example's directory so each
    # example gets its own namespaced volumes. Derive from cwd rather than
    # the compose path — on first boot Aegra has not generated the compose
    # yet, so ``current_compose`` is ``None``.
    compose_env = os.environ.copy()
    compose_env["COMPOSE_PROJECT_NAME"] = cwd.name

    try:
        if verbose:
            click.echo(f"Starting dev server on port {port}... (verbose)")
            exit_code = subprocess.call(cmd, env=compose_env)
        else:
            click.echo(f"Starting dev server on port {port}...")
            exit_code = _run_curated(cmd, port, env=compose_env)
    finally:
        # Tear down the current example's docker stack on exit so
        # Postgres/Redis containers don't linger after Ctrl-C. Volumes
        # are preserved; re-entering the example keeps its data.
        if current_compose is not None:
            click.echo(f"Tearing down: {current_compose}")
            _stop_compose_containers(current_compose)
            _clear_active_example()
    sys.exit(exit_code)


@dev.command("down")
def dev_down() -> None:
    """Tear down the most recently started example's docker stack.

    Reads ``~/.monet/state.json`` for the last compose file and stops
    its declared ``container_name`` services. Volumes are preserved; re-
    entering the example keeps its Postgres data.
    """
    compose = _read_active_example()
    if compose is None:
        click.echo("No active monet example recorded in ~/.monet/state.json.")
        return
    if not compose.exists():
        click.echo(f"Recorded compose file no longer exists: {compose}")
        _clear_active_example()
        return

    click.echo(f"Tearing down: {compose}")
    if _stop_compose_containers(compose):
        _clear_active_example()
    else:
        click.echo("  (no running containers found — clearing active example pointer)")
        _clear_active_example()


# ── state tracking + teardown ───────────────────────────────────────


def _current_compose_path(aegra_config: Path) -> Path | None:
    """Return the compose file a given aegra.json implies, if any.

    Aegra's convention is ``<config-parent>/.monet/docker-compose.yml``
    OR — when the aegra.json is itself inside ``.monet/`` — its sibling.
    Returns ``None`` when no compose file is found.
    """
    candidates = [
        aegra_config.parent / "docker-compose.yml",
        aegra_config.parent.parent / "docker-compose.yml",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return None


def _read_active_state() -> dict[str, Any]:
    """Return the parsed ``state.json`` contents, or an empty dict."""
    sf = state_file()
    if not sf.exists():
        return {}
    try:
        data = json.loads(sf.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_active_example() -> Path | None:
    """Return the compose path of the currently active example, if any."""
    compose = _read_active_state().get("active_compose")
    if not compose:
        return None
    return Path(compose)


def _read_active_pid() -> int | None:
    """Return the PID of the currently active ``monet dev`` process, if any."""
    raw = _read_active_state().get("pid")
    if isinstance(raw, int):
        return raw
    return None


def _record_active_example(compose: Path) -> None:
    """Persist ``compose`` + this process's PID as the active example."""
    sf = state_file()
    payload: dict[str, Any] = {
        "active_compose": str(compose),
        "pid": os.getpid(),
    }
    with contextlib.suppress(OSError):
        sf.write_text(json.dumps(payload, indent=2))


def _clear_active_example() -> None:
    """Forget the active example pointer."""
    sf = state_file()
    with contextlib.suppress(OSError):
        sf.write_text(json.dumps({}))


def _wipe_example_volumes(project: str) -> None:
    """Remove the Postgres + Redis named volumes for *project*.

    Volume names follow Docker Compose's convention
    ``<project>_<volume_name>``. ``docker volume rm`` is idempotent
    via a suppressed non-zero exit, so missing volumes no-op.
    """
    volumes = [f"{project}_postgres_data", f"{project}_redis_data"]
    for name in volumes:
        subprocess.run(
            ["docker", "volume", "rm", name],
            check=False,
            capture_output=True,
        )
    click.echo(f"Wiped volumes: {', '.join(volumes)}")


def _kill_process_tree(pid: int) -> None:
    """Best-effort kill of a process + its descendants.

    Silently no-ops if the process no longer exists. Used when
    reclaiming ports from a stale ``monet dev`` whose containers were
    taken down but whose parent process (Aegra + uvicorn reloader)
    still holds :2026.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            check=False,
            capture_output=True,
        )
    else:
        import signal

        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)


def _teardown_previous(current: Path) -> None:
    """If a different example was last active, stop its containers.

    Parses ``container_name:`` entries from the previous compose file and
    ``docker stop`` + ``docker rm`` each one. Avoids ``docker compose down``
    because the compose file may reference env files that were not committed
    and ``compose down`` refuses to parse without them. Volumes are not
    touched, so re-entering the previous example retains its Postgres data.
    """
    previous = _read_active_example()
    if previous is None or previous.resolve() == current.resolve():
        return
    if not previous.exists():
        _clear_active_example()
        return
    click.echo(f"Tearing down previous example: {previous}")
    _stop_compose_containers(previous)


# Regex for container_name lines. Matches quoted and bare values.
_CONTAINER_NAME_RE = re.compile(
    r"""^\s*container_name:\s*['"]?([A-Za-z0-9_.-]+)['"]?\s*$""",
    re.MULTILINE,
)


def _stop_compose_containers(compose: Path) -> bool:
    """Stop and remove each ``container_name`` declared in ``compose``.

    Returns True if at least one container was stopped, False if there was
    nothing running to stop (silent success either way — we use this from
    both the explicit ``dev down`` command and the implicit teardown in
    ``dev``).
    """
    try:
        text = compose.read_text(encoding="utf-8")
    except OSError:
        return False
    names = _CONTAINER_NAME_RE.findall(text)
    stopped = 0
    for name in names:
        # docker stop is idempotent-ish: returns non-zero when the
        # container doesn't exist, which we intentionally swallow. rm -f
        # then removes the stopped container so a subsequent compose up
        # can recreate it with fresh env vars.
        stop = subprocess.run(
            ["docker", "stop", name],
            check=False,
            capture_output=True,
            text=True,
        )
        if stop.returncode == 0:
            stopped += 1
            subprocess.run(
                ["docker", "rm", "-f", name],
                check=False,
                capture_output=True,
            )
    return stopped > 0


def _run_curated(
    cmd: list[str],
    port: int,
    env: dict[str, str] | None = None,
) -> int:
    """Run the Aegra subprocess and emit a simplified, monet-branded log.

    Readiness is detected by probing the server's ``/health`` endpoint.
    Stdout is still consumed so unexpected errors pass through to the user,
    but known-noisy log lines are dropped.
    """
    # Force utf-8 on the child process stdout so emojis in langgraph output
    # don't crash on Windows cp1252 consoles before we get a chance to filter.
    # PYTHONUNBUFFERED=1 ensures any pass-through error lines flush promptly.
    env = dict(env) if env is not None else os.environ.copy()
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
            # Unknown line — pass through to the user.  Replace chars
            # the console codec can't handle (e.g. emoji on Windows cp1252).
            try:
                click.echo(line)
            except UnicodeEncodeError:
                safe = line.encode(
                    sys.stdout.encoding or "utf-8", errors="replace"
                ).decode(sys.stdout.encoding or "utf-8", errors="replace")
                click.echo(safe)
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
    # Only suppress uvicorn-style HTTP access info lines (very noisy, little
    # signal). We intentionally do NOT drop plain "INFO:" — agent and
    # planner logs are emitted at INFO level and hiding them silently
    # masks defects like planner_error.
    "INFO:     127.0.0.1",
    "INFO:     Started server",
    "INFO:     Waiting for",
    "INFO:     Application startup",
    "INFO:     Shutting down",
    "[uvicorn.error]",
    "[alembic.runtime.migration]",
    "[app.access_logs]",
    "[google_genai.models]",
)

# Substrings that MUST pass through even if a broader rule would drop the
# line. Anything agent-originated or explicitly error-tagged shows up
# verbatim so real failures are visible to the user.
_KEEP_SUBSTRINGS: tuple[str, ...] = (
    "monet.agent",
    "planner_error",
    "ERROR",
    "Traceback",
    "RuntimeError",
)

# Box-drawing characters used in ASCII welcome banners. Lines made up
# entirely of these (plus whitespace) are banner art and get dropped.
_BANNER_CHARS = frozenset("╔╦╗║╚╩╝╠╣═╬┌┐└┘│─├┤┬┴┼")


def _should_drop(line: str) -> bool:
    """True if this langgraph stdout line should be hidden in curated mode."""
    stripped = line.strip()
    if not stripped:
        return True
    # Keep-list trumps drop-list: real errors and agent logs always pass.
    if any(sub in line for sub in _KEEP_SUBSTRINGS):
        return False
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


def _check_ports_free(dev_port: int) -> None:
    """Ensure the standard monet dev ports are free, reclaiming if needed.

    If any of the standard ports (2026, 5432, 6379) is already bound
    AND ``~/.monet/state.json`` points to a known monet example, tear
    that example's containers down and proceed — this handles the
    common "Ctrl-C didn't fully close the last run" case on Windows.

    If ports are still bound after that (or were bound by an unrelated
    process to begin with), fail fast with a clear message.
    """
    busy = _busy_standard_ports(dev_port)
    if not busy:
        return

    previous = _read_active_example()
    previous_pid = _read_active_pid()
    if previous is not None and previous.exists():
        click.echo(f"Reclaiming standard ports from previous monet dev: {previous}")
        # Kill the parent process tree first — that holds :2026 via
        # uvicorn. Stopping only the docker containers leaves a zombie
        # Aegra bound to the host port.
        if previous_pid is not None:
            _kill_process_tree(previous_pid)
        _stop_compose_containers(previous)
        _clear_active_example()
        # Give the OS a beat to release the host-port bindings before
        # re-checking. One second is enough in practice on Windows.
        time.sleep(1.0)
        busy = _busy_standard_ports(dev_port)
        if not busy:
            return

    lines = [f"  :{p} ({label})" for p, label in busy]
    detail = "\n".join(lines)
    raise click.ClickException(
        "Standard monet dev ports are already in use:\n"
        f"{detail}\n\n"
        "No tracked monet example was able to free them — another "
        "process is holding these ports. Stop it (check `docker ps` "
        "and any running `monet dev`), then retry."
    )


def _busy_standard_ports(dev_port: int) -> list[tuple[int, str]]:
    """Return the (port, label) pairs currently bound on localhost."""
    candidates = [
        (dev_port, "Aegra dev server"),
        (STANDARD_POSTGRES_PORT, "Postgres"),
        (STANDARD_REDIS_PORT, "Redis"),
    ]
    return [(p, label) for p, label in candidates if _port_in_use(p)]


def _port_in_use(port: int) -> bool:
    """True if ``port`` is currently bound on localhost."""
    # Use connect() rather than bind() — bind() on Windows succeeds even
    # for ports held by another process due to SO_REUSEADDR quirks.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect(("127.0.0.1", port))
        except (ConnectionRefusedError, TimeoutError, OSError):
            return False
        return True
