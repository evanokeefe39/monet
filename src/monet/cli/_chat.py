"""monet chat — interactive multi-turn conversation REPL.

Each session is backed by an Aegra thread. Messages persist across
CLI restarts via LangGraph checkpoint state. The ``/run`` command
dispatches work through the default monet pipeline inline.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from monet.client import MonetClient

from monet._ports import STANDARD_DEV_PORT
from monet.cli._render import render_run_table
from monet.cli._setup import check_env
from monet.config import MONET_API_KEY, MONET_SERVER_URL


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
def chat(
    url: str,
    api_key: str | None,
    force_new: bool,
    list_sessions: bool,
    resume_id: str | None,
    session_name: str | None,
    graph_override: str | None,
) -> None:
    """Interactive multi-turn conversation with the monet platform."""
    import contextlib

    check_env()
    with contextlib.suppress(KeyboardInterrupt):
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


async def _chat_main(
    url: str,
    api_key: str | None,
    force_new: bool,
    list_sessions: bool,
    resume_id: str | None,
    session_name: str | None,
    graph_override: str | None,
) -> None:
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

    client = MonetClient(url, api_key=api_key, graph_ids=graph_ids)

    if list_sessions:
        chats = await client.list_chats()
        if not chats:
            click.echo("No chat sessions found.")
            return
        click.echo(f"{'THREAD ID':<40} {'NAME':<20} {'MSGS':<6} {'LAST ACTIVE'}")
        click.echo("-" * 76)
        for c in chats:
            from monet.cli._render import format_age

            age = format_age(c.updated_at)
            name = c.name or "(unnamed)"
            click.echo(f"{c.thread_id:<40} {name:<20} {c.message_count:<6} {age}")
        return

    thread_id: str | None = None

    if resume_id:
        thread_id = resume_id
    elif session_name:
        chats = await client.list_chats()
        for c in chats:
            if c.name == session_name:
                thread_id = c.thread_id
                break
        if thread_id is None:
            thread_id = await client.create_chat(name=session_name)
            click.echo(f"Created session '{session_name}'", err=True)
    elif force_new:
        thread_id = await client.create_chat()
    else:
        thread_id = await client.get_most_recent_chat()
        if thread_id is None:
            thread_id = await client.create_chat()
            click.echo("Started new conversation.", err=True)

    click.echo(f"Session: {thread_id}", err=True)

    history = await client.get_chat_history(thread_id)
    if history:
        click.echo(f"({len(history)} messages in history)", err=True)
        recent = history[-4:]
        for msg in recent:
            role = msg.get("role", "?")
            content = str(msg.get("content", ""))[:200]
            if role == "user":
                click.secho(f"> {content}", dim=True)
            elif role == "assistant":
                click.secho(f"  {content}", dim=True)
        if len(history) > 4:
            click.secho("  ...", dim=True)
        click.echo()

    await _chat_repl(client, thread_id)


async def _chat_repl(client: MonetClient, thread_id: str) -> None:
    """Main REPL loop for the chat session."""
    while True:
        try:
            line = click.prompt("", prompt_suffix="> ", default="", show_default=False)
        except (EOFError, KeyboardInterrupt):
            click.echo()
            return

        line = line.strip()
        if not line:
            continue

        if line.startswith("/"):
            should_exit = await _handle_slash_command(client, thread_id, line)
            if should_exit:
                return
            continue

        try:
            async for token in client.send_message(thread_id, line):
                click.echo(token, nl=False)
            click.echo()
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)


async def _handle_slash_command(client: MonetClient, thread_id: str, line: str) -> bool:
    """Dispatch a slash command. Returns True if the REPL should exit."""
    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit"):
        return True

    if cmd == "/name":
        if not arg:
            click.echo("Usage: /name <name>")
            return False
        await client.rename_chat(thread_id, arg)
        click.echo(f"Session renamed to '{arg}'.", err=True)
        return False

    if cmd == "/runs":
        summaries = await client.list_runs()
        render_run_table(summaries)
        return False

    if cmd == "/graphs":
        try:
            graphs = await client.list_graphs()
            if not graphs:
                click.echo("No graphs found on server.")
            else:
                click.echo("Available graphs:")
                for g in graphs:
                    click.echo(f"  {g}")
        except Exception as exc:
            click.echo(f"Error listing graphs: {exc}", err=True)
        return False

    if cmd == "/history":
        history = await client.get_chat_history(thread_id)
        if not history:
            click.echo("No messages yet.")
            return False
        for msg in history:
            role = msg.get("role", "?")
            content = str(msg.get("content", ""))
            if role == "user":
                click.secho(f"> {content}", bold=True)
            elif role == "assistant":
                click.echo(f"  {content}")
            elif role == "system":
                click.secho(f"  [context] {content[:100]}", dim=True)
        return False

    click.echo(f"Unknown command: {cmd}")
    click.echo("Commands: /name, /runs, /graphs, /history, /quit")
    return False
