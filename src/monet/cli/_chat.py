"""monet chat — interactive multi-turn conversation REPL.

Each session is backed by an Aegra thread. Messages persist across
CLI restarts via LangGraph checkpoint state. The ``/run`` command
dispatches work through the default monet pipeline inline.
"""

from __future__ import annotations

import asyncio
import contextlib
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

    # Discover server-side agent capabilities so `/<agent>:<command>`
    # slash commands resolve to direct invocations. Failure to reach
    # the manifest is non-fatal — chat still works without them.
    capabilities: list[dict[str, object]] = []
    try:
        raw_caps = await client.list_capabilities()
        capabilities = [dict(c) for c in raw_caps]
    except Exception as exc:
        click.secho(f"(agent discovery failed: {exc})", dim=True, err=True)

    if capabilities:
        preview = ", ".join(
            f"/{c.get('agent_id')}:{c.get('command')}" for c in capabilities[:5]
        )
        more = "" if len(capabilities) <= 5 else f" (+{len(capabilities) - 5} more)"
        click.secho(f"Agents available: {preview}{more}", dim=True, err=True)

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

    await _chat_repl(client, thread_id, capabilities)


async def _chat_repl(
    client: MonetClient,
    thread_id: str,
    capabilities: list[dict[str, object]],
) -> None:
    """Main REPL loop for the chat session."""
    cap_index = {f"/{c.get('agent_id')}:{c.get('command')}": c for c in capabilities}

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
            cmd_head = line.split(maxsplit=1)[0].lower()
            if cmd_head in cap_index:
                await _invoke_agent_from_chat(
                    client, thread_id, cap_index[cmd_head], line
                )
                continue
            should_exit = await _handle_slash_command(
                client, thread_id, line, cap_index
            )
            if should_exit:
                return
            continue

        try:
            async for token in client.send_message(thread_id, line):
                click.echo(token, nl=False)
            click.echo()
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)


async def _invoke_agent_from_chat(
    client: MonetClient,
    thread_id: str,
    capability: dict[str, object],
    line: str,
) -> None:
    """Run ``/<agent>:<command> <task>`` in-REPL and append result to thread."""
    import json as _json

    agent_id = str(capability.get("agent_id", ""))
    command = str(capability.get("command", ""))
    task = line.split(maxsplit=1)[1] if " " in line else ""
    click.secho(f"Invoking {agent_id}:{command}…", dim=True, err=True)
    try:
        result = await client.invoke_agent(agent_id, command, task=task)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        return
    success = result.get("success")
    status_color = "green" if success else "red"
    click.secho(
        f"{agent_id}:{command} {'ok' if success else 'failed'}",
        fg=status_color,
    )
    output = result.get("output")
    if output:
        rendered = (
            output
            if isinstance(output, str)
            else _json.dumps(output, indent=2, default=str)
        )
        click.echo(rendered)
        # Attach a short summary back into the chat thread as a
        # system message so the conversation context knows it happened.
        summary = f"[{agent_id}:{command}] {str(rendered)[:400]}"
        with contextlib.suppress(Exception):
            await client.send_context(thread_id, summary)


async def _handle_slash_command(
    client: MonetClient,
    thread_id: str,
    line: str,
    cap_index: dict[str, dict[str, object]],
) -> bool:
    """Dispatch a slash command. Returns True if the REPL should exit."""
    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit"):
        return True

    if cmd == "/help":
        click.echo("Commands:")
        click.echo("  /name <name>         rename session")
        click.echo("  /runs                list recent runs")
        click.echo("  /graphs              list server graphs")
        click.echo("  /history             show conversation history")
        click.echo("  /quit, /exit         leave the REPL")
        if cap_index:
            click.echo("Agents:")
            for slash, cap in sorted(cap_index.items()):
                desc = str(cap.get("description", "")) or ""
                click.echo(f"  {slash} <task>" + (f"  — {desc}" if desc else ""))
        return False

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
    click.echo("Commands: /help, /name, /runs, /graphs, /history, /quit")
    return False
