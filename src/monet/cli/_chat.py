"""monet chat — interactive multi-turn conversation REPL.

Each session is backed by an Aegra thread. Messages persist across
CLI restarts via LangGraph checkpoint state. The ``/run`` command
dispatches work through the monet pipeline inline.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from monet.client import MonetClient

from monet.cli._render import (
    prompt_execution_decision,
    prompt_plan_decision,
    render_event,
    render_run_table,
)
from monet.cli._setup import check_env


@click.command()
@click.option(
    "--url",
    default="http://localhost:2026",
    envvar="MONET_SERVER_URL",
    help="Aegra server URL.",
)
@click.option("--new", "force_new", is_flag=True, help="Start a new conversation.")
@click.option("--list", "list_sessions", is_flag=True, help="List saved conversations.")
@click.option("--resume", "resume_id", default=None, help="Resume a specific thread.")
@click.option("--session", "session_name", default=None, help="Named session.")
@click.option("--graph", "graph_override", default=None, help="Chat graph ID.")
def chat(
    url: str,
    force_new: bool,
    list_sessions: bool,
    resume_id: str | None,
    session_name: str | None,
    graph_override: str | None,
) -> None:
    """Interactive multi-turn conversation with the monet platform.

    Sessions persist across CLI restarts. Use /run <task> to dispatch
    work through the monet pipeline from within the conversation.
    """
    import contextlib

    check_env()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            _chat_main(
                url,
                force_new,
                list_sessions,
                resume_id,
                session_name,
                graph_override,
            )
        )


async def _chat_main(
    url: str,
    force_new: bool,
    list_sessions: bool,
    resume_id: str | None,
    session_name: str | None,
    graph_override: str | None,
) -> None:
    from monet._graph_config import load_graph_roles
    from monet.client import MonetClient

    graph_ids = load_graph_roles()
    if graph_override:
        graph_ids["chat"] = graph_override

    client = MonetClient(url, graph_ids=graph_ids)

    # --list: show saved conversations and exit.
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

    # Resolve thread_id: --resume, --session, --new, or most recent.
    thread_id: str | None = None

    if resume_id:
        thread_id = resume_id
    elif session_name:
        # Search for existing named session.
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
        # Resume most recent, or create new if none.
        thread_id = await client.get_most_recent_chat()
        if thread_id is None:
            thread_id = await client.create_chat()
            click.echo("Started new conversation.", err=True)

    click.echo(f"Session: {thread_id}", err=True)

    # Show recent history on resume.
    history = await client.get_chat_history(thread_id)
    if history:
        click.echo(f"({len(history)} messages in history)", err=True)
        # Show last few messages for context.
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

        # Slash commands.
        if line.startswith("/"):
            should_exit = await _handle_slash_command(client, thread_id, line)
            if should_exit:
                return
            continue

        # Send message and stream response.
        try:
            async for token in client.send_message(thread_id, line):
                click.echo(token, nl=False)
            click.echo()  # Final newline after response.
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

    if cmd == "/run":
        if not arg:
            click.echo("Usage: /run <task>")
            return False
        await _handle_inline_run(client, thread_id, arg)
        return False

    if cmd == "/runs":
        summaries = await client.list_runs()
        render_run_table(summaries)
        return False

    if cmd == "/attach":
        if not arg:
            click.echo("Usage: /attach <run_id>")
            return False
        try:
            detail = await client.get_run(arg)
            # Build a summary of the run results.
            parts_list: list[str] = [f"Attached run {arg} (status: {detail.status})"]
            if detail.routing_skeleton:
                goal = detail.routing_skeleton.get("goal", "")
                if goal:
                    parts_list.append(f"Goal: {goal}")
            for wr in detail.wave_results:
                agent = wr.get("agent_id", "?")
                output = str(wr.get("output", ""))[:200]
                parts_list.append(f"[{agent}] {output}")
            summary = "\n".join(parts_list)
            await client.send_context(thread_id, summary)
            click.echo(f"Attached run {arg} to conversation.", err=True)
        except Exception as exc:
            click.echo(f"Error attaching run: {exc}", err=True)
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
    click.echo("Commands: /name, /run, /runs, /attach, /graphs, /history, /quit")
    return False


async def _handle_inline_run(client: MonetClient, thread_id: str, task: str) -> None:
    """Dispatch a monet run inline from the chat REPL."""
    from monet.client._events import (
        ExecutionInterrupt,
        PlanInterrupt,
        RunComplete,
        RunFailed,
    )

    click.echo(f"Running: {task}", err=True)
    run_id: str | None = None

    try:
        async for event in client.run(task):
            render_event(event)

            if run_id is None and hasattr(event, "run_id"):
                run_id = event.run_id

            # HITL: plan approval.
            if isinstance(event, PlanInterrupt) and run_id:
                decision = prompt_plan_decision()
                if decision == "approve":
                    async for follow_up in client.approve_plan(run_id):
                        render_event(follow_up)
                elif decision == "revise":
                    feedback = click.prompt("Feedback")
                    async for follow_up in client.revise_plan(run_id, feedback):
                        render_event(follow_up)
                elif decision == "reject":
                    click.secho("Run rejected.", fg="red")
                    return
                break

            # HITL: execution interrupt.
            if isinstance(event, ExecutionInterrupt) and run_id:
                exec_decision = prompt_execution_decision()
                if exec_decision == "retry":
                    async for follow_up in client.retry_wave(run_id):
                        render_event(follow_up)
                else:
                    await client.abort_run(run_id)
                    click.secho("Run aborted.", fg="red")
                break

            if isinstance(event, RunComplete | RunFailed):
                break

        # Send summary back to chat context.
        if run_id:
            try:
                detail = await client.get_run(run_id)
                summary_parts: list[str] = [
                    f"Completed run {run_id} (status: {detail.status})"
                ]
                if detail.routing_skeleton:
                    goal = detail.routing_skeleton.get("goal", "")
                    if goal:
                        summary_parts.append(f"Goal: {goal}")
                for wr in detail.wave_results[:5]:
                    agent = wr.get("agent_id", "?")
                    output = str(wr.get("output", ""))[:200]
                    summary_parts.append(f"[{agent}] {output}")
                await client.send_context(thread_id, "\n".join(summary_parts))
            except Exception:
                pass  # Best-effort context injection.

    except Exception as exc:
        click.echo(f"Run error: {exc}", err=True)
