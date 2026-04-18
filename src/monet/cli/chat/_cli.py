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

_DEFAULT_LOG_PATH = Path.cwd() / ".cli-logs" / "chat.log"


def _configure_chat_logging(path: Path) -> Path:
    """Route Python logging to *path* so the TUI has persistent observability.

    Textual swallows stdout/stderr while the app is running, so anything
    written through ``logging`` would otherwise vanish. A file handler
    preserves tracebacks from the client, server-side retries, and any
    other module that logs. Existing handlers are removed so we don't
    double-log once the TUI takes over the terminal.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
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
    root.setLevel(logging.INFO)
    return path


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
    "--log-file",
    "log_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Write chat-side logs here. Defaults to ./.cli-logs/chat.log. "
        "Textual swallows stdout while the TUI runs, so this file is the "
        "only way to see chat-side errors."
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
    log_file: Path | None,
) -> None:
    """Interactive multi-turn conversation with the monet platform."""
    check_env()
    resolved_log = _configure_chat_logging(log_file or _DEFAULT_LOG_PATH)
    if not list_sessions:
        click.secho(f"chat logs → {resolved_log}", dim=True, err=True)
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

    from monet.config._user_chat import load_user_chat_style

    app = ChatApp(
        client=client,
        thread_id=thread_id,
        slash_commands=slash_commands,
        history=history,
        style=load_user_chat_style(),
    )
    await app.run_async()


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
