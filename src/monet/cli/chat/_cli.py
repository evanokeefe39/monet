"""``monet chat`` — interactive Textual TUI backed by an Aegra thread.

Thin Click entry point that resolves the target thread, pulls the live
slash-command list, loads history, then hands off to
:class:`~monet.cli.chat._app.ChatApp` for the interactive session. The
``--list`` path stays non-interactive (prints a table) so scripted uses
don't need a TTY.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
import httpx

if TYPE_CHECKING:
    from monet.client import MonetClient

from monet._ports import STANDARD_DEV_PORT
from monet.cli._namegen import random_chat_name
from monet.cli._setup import check_env
from monet.config import MONET_API_KEY, MONET_SERVER_URL

_DEFAULT_LOG_DIR = Path.cwd() / ".cli-logs"


def _log_filename(thread_id: str) -> str:
    """Build ``<ISO-timestamp>_<thread_id>.log`` name for this session."""
    ts = datetime.now(UTC).strftime("%Y%m%d")
    tid = thread_id[:12] if thread_id else "new"
    return f"{ts}_{tid}.log"


def _configure_chat_logging(
    log_dir: Path,
    thread_id: str,
) -> Path:
    """Route Python logging to a per-thread file under *log_dir*.

    Only called when ``--verbose`` is active. Textual swallows
    stdout/stderr while the app is running, so a file handler is the
    only way to preserve tracebacks and diagnostics.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / _log_filename(thread_id)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    debug_log = Path.home() / ".monet" / "chat-debug.log"
    debug_log.parent.mkdir(parents=True, exist_ok=True)
    sse_handler = logging.FileHandler(debug_log, encoding="utf-8")
    sse_handler.setLevel(logging.DEBUG)
    sse_handler.setFormatter(
        logging.Formatter("%(asctime)s SSE: %(message)s", datefmt="%H:%M:%S")
    )
    sse_logger = logging.getLogger("monet.cli.chat.sse")
    sse_logger.setLevel(logging.DEBUG)
    sse_logger.addHandler(sse_handler)
    sse_logger.propagate = False

    return path


def _suppress_logging() -> None:
    """Silence all Python logging when not in verbose mode."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(logging.NullHandler())
    root.setLevel(logging.CRITICAL)


@click.command()
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
@click.option("--new", "force_new", is_flag=True, help="Start a new conversation.")
@click.option("--list", "list_sessions", is_flag=True, help="List saved conversations.")
@click.option("--resume", "resume_id", default=None, help="Resume a specific thread.")
@click.option("--session", "session_name", default=None, help="Named session.")
@click.option(
    "--graph",
    "graph_override",
    default=None,
    help=(
        "Name of a declared entrypoint in monet.toml "
        "(e.g. 'chat'), or a raw graph id. Omit to use the default chat graph."
    ),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help=(
        "Write per-thread debug logs to .cli-logs/ and raw SSE"
        " events to ~/.monet/chat-debug.log."
    ),
)
def chat(
    url: str,
    api_key: str | None,
    force_new: bool,
    list_sessions: bool,
    resume_id: str | None,
    session_name: str | None,
    graph_override: str | None,
    verbose: bool,
) -> None:
    """Interactive multi-turn conversation with the monet platform."""
    check_env()
    if not verbose:
        _suppress_logging()
    try:
        asyncio.run(
            _chat_main(
                url,
                api_key,
                force_new,
                list_sessions,
                resume_id,
                session_name,
                graph_override,
                verbose,
            )
        )
    except KeyboardInterrupt:
        return
    except (httpx.ConnectError, httpx.TimeoutException, ConnectionError, OSError):
        click.secho(
            f"Cannot reach monet server at {url}.",
            err=True,
            fg="red",
        )
        click.secho(
            "Start it with `monet dev` and try again.",
            err=True,
            dim=True,
        )
        raise SystemExit(2) from None


async def _chat_main(
    url: str,
    api_key: str | None,
    force_new: bool,
    list_sessions: bool,
    resume_id: str | None,
    session_name: str | None,
    graph_override: str | None,
    verbose: bool,
) -> None:
    from monet.cli._run import _preflight_server
    from monet.cli.chat._app import ChatApp
    from monet.client import MonetClient
    from monet.config import load_entrypoints, load_graph_roles

    entrypoints = load_entrypoints()
    graph_ids = load_graph_roles()

    key = graph_override or "chat"
    ep = entrypoints.get(key)
    if ep is not None:
        graph_ids["chat"] = ep["graph"]
    elif graph_override:
        graph_ids["chat"] = graph_override

    # Fail fast: server unreachable → clear error before TUI swallows stdout.
    health_error = await _preflight_server(url, api_key=api_key)
    if health_error:
        click.secho(health_error, err=True, fg="red")
        raise SystemExit(2)

    client = MonetClient(url, api_key=api_key, graph_ids=graph_ids)

    # Fail fast: chat graph must be registered on the server. The TUI
    # has no useful mode without it — every action it takes streams
    # against this graph id. If the resolved id is not in the server's
    # assistant list the server will fail later with an opaque error;
    # surface it here with an actionable message instead.
    chat_graph_id = graph_ids.get("chat") or "chat"
    try:
        registered = await client.list_graphs()
    except Exception as exc:
        click.secho(
            f"Could not enumerate graphs on {url} ({exc}).",
            err=True,
            fg="red",
        )
        raise SystemExit(2) from None
    if chat_graph_id not in registered:
        click.secho(
            (f"Chat graph '{chat_graph_id}' is not registered on the server at {url}."),
            err=True,
            fg="red",
        )
        click.secho(
            (
                "Register it via `aegra.json`, set "
                '`[chat] graph = "<module>:<factory>"` in monet.toml, '
                "or pass --graph to pick a different entrypoint. "
                f"Graphs available now: {', '.join(registered) or '(none)'}."
            ),
            err=True,
            dim=True,
        )
        raise SystemExit(2)

    if list_sessions:
        await _render_session_list(client)
        return

    thread_id = await _resolve_thread(
        client,
        resume_id=resume_id,
        session_name=session_name,
        force_new=force_new,
    )

    log_path: Path | None = None
    if verbose:
        log_path = _configure_chat_logging(_DEFAULT_LOG_DIR, thread_id)
        if not list_sessions:
            click.secho(f"chat logs → {log_path}", dim=True, err=True)

    slash_commands: list[str] = []
    try:
        slash_commands = await client.slash_commands()
    except Exception as exc:
        click.secho(f"(agent discovery failed: {exc})", dim=True, err=True)

    history: list[dict[str, object]] = []
    if thread_id:
        try:
            history = list(await client.chat.get_chat_history(thread_id))
        except Exception as exc:
            click.secho(f"(history load failed: {exc})", dim=True, err=True)

    progress = []
    if thread_id:
        try:
            progress = await client.get_thread_progress(thread_id)
        except Exception as exc:
            click.secho(f"(progress load failed: {exc})", dim=True, err=True)

    app = ChatApp(
        client=client,
        thread_id=thread_id,
        slash_commands=slash_commands,
        history=history,
        progress=progress,
    )
    await app.run_async()
    final_tid = app.thread_id
    if final_tid:
        click.secho(
            f"resume with: monet chat --resume {final_tid}",
            dim=True,
            err=True,
        )
    if app._crash_error is not None:
        err = app._crash_error
        click.secho(
            f"chat crashed ({type(err).__name__}: {err}).",
            err=True,
            fg="red",
        )
        if log_path:
            click.secho(f"See full traceback in {log_path}.", err=True, dim=True)
        else:
            click.secho(
                "Re-run with --verbose to capture full traceback.",
                err=True,
                dim=True,
            )
        raise SystemExit(1)


async def _render_session_list(client: MonetClient) -> None:
    chats = await client.chat.list_chats()
    if not chats:
        click.echo("No chat sessions found.")
        return
    from monet.cli._render import format_age

    click.echo(f"{'THREAD ID':<40} {'NAME':<20} {'MSGS':<6} {'LAST ACTIVE'}")
    click.echo("-" * 76)
    for c in chats:
        age = format_age(c.updated_at)
        name = c.name or "(unnamed)"
        click.echo(f"{c.thread_id:<40} {name:<20} {c.message_count:<6} {age}")


async def _resolve_thread(
    client: MonetClient,
    *,
    resume_id: str | None,
    session_name: str | None,
    force_new: bool,
) -> str:
    """Pick the thread the TUI will attach to.

    Explicit ``--resume`` wins, then named ``--session`` (created on
    miss), then ``--new`` creates eagerly. Default path returns an
    empty string — ChatApp creates the thread lazily on first user
    message so empty sessions don't spam the thread list.
    """
    if resume_id:
        return resume_id
    if session_name:
        chats = await client.chat.list_chats()
        for c in chats:
            if c.name == session_name:
                return c.thread_id
        thread_id = await client.chat.create_chat(name=session_name)
        click.secho(f"Created session '{session_name}'", dim=True, err=True)
        return thread_id
    if force_new:
        return await client.chat.create_chat(name=random_chat_name())
    return ""
