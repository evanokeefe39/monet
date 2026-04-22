"""Slash command dispatch for the monet chat TUI.

Each TUI-local command is a small async function. The dispatcher checks
whether the text matches a local command; if not, it falls through so
the text is sent to the server as a chat message.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from monet.cli.chat._transcript import Transcript
    from monet.client import MonetClient

_log = logging.getLogger("monet.cli.chat")


@runtime_checkable
class AppActions(Protocol):
    """Callback surface the App exposes to slash commands."""

    def get_thread_id(self) -> str: ...
    def set_thread_id(self, tid: str) -> None: ...
    def set_title(self, title: str) -> None: ...
    def update_status(self, **kwargs: Any) -> None: ...
    def copy_to_clipboard(self, text: str) -> None: ...
    def exit_app(self) -> None: ...
    def push_screen_by_name(self, name: str) -> None: ...


class CommandContext:
    """Minimal context passed to each slash command handler."""

    def __init__(
        self,
        *,
        client: MonetClient,
        transcript: Transcript,
        thread_id: str,
        server_slash_commands: list[str],
        app: AppActions,
    ) -> None:
        self.client = client
        self.transcript = transcript
        self.thread_id = thread_id
        self.server_slash_commands = server_slash_commands
        self.app = app


async def dispatch_slash(ctx: CommandContext, text: str) -> bool:
    """Dispatch a TUI-local slash command. Returns True if handled."""
    head, _, rest = text.partition(" ")
    arg = rest.strip()

    if head in {"/quit", "/exit"}:
        ctx.app.exit_app()
        return True
    if head in {"/threads", "/agents", "/artifacts", "/runs"}:
        ctx.app.push_screen_by_name(head.lstrip("/"))
        return True
    if head in {"/new", "/clear"}:
        await _cmd_new(ctx)
        return True
    if head == "/switch":
        await _cmd_switch(ctx, arg)
        return True
    if head == "/rename":
        if arg:
            await _cmd_rename(ctx, arg)
        else:
            ctx.transcript.append("[info] usage: /rename <name>")
        return True
    if head == "/copy":
        _cmd_copy(ctx, arg)
        return True
    if head == "/shortcuts":
        ctx.app.push_screen_by_name("shortcuts")
        return True
    if head == "/help":
        _cmd_help(ctx)
        return True
    return False


async def _cmd_new(ctx: CommandContext) -> None:
    from monet.cli._namegen import random_chat_name

    name = random_chat_name()
    try:
        new_id = await ctx.client.chat.create_chat(name=name)
    except Exception as exc:
        ctx.transcript.append(f"[error] /new failed: {exc}")
        return
    ctx.app.set_thread_id(new_id)
    ctx.app.set_title(f"monet chat · {name}")
    ctx.app.update_status(thread_name=name)
    ctx.transcript.clear()
    ctx.transcript.append(f"[info] new thread · {name} · {new_id[:8]}")


async def _cmd_switch(ctx: CommandContext, target: str) -> None:
    if not target:
        ctx.transcript.append("[info] usage: /switch <thread_id>")
        return
    try:
        history = await ctx.client.chat.get_chat_history(target)
    except Exception as exc:
        ctx.transcript.append(f"[error] /switch failed: {exc}")
        return
    ctx.app.set_thread_id(target)
    ctx.app.set_title(f"monet chat · {target}")
    ctx.app.update_status(thread_name="")
    ctx.transcript.clear()
    ctx.transcript.append(f"[info] switched to {target}")
    for msg in history:
        role = str(msg.get("role") or "user")
        content = str(msg.get("content") or "")
        ctx.transcript.append(f"[{role}] {content}", markdown=(role == "assistant"))
    if not history:
        pass


async def _cmd_rename(ctx: CommandContext, name: str) -> None:
    thread_id = ctx.app.get_thread_id()
    if not thread_id:
        ctx.transcript.append("[error] no active thread to rename")
        return
    try:
        await ctx.client.chat.rename_chat(thread_id, name)
    except Exception as exc:
        ctx.transcript.append(f"[error] rename failed: {exc}")
        return
    ctx.transcript.append(f"[info] thread renamed to {name}")
    ctx.app.update_status(thread_name=name)


def _cmd_copy(ctx: CommandContext, arg: str = "") -> None:
    if arg.strip() == "all":
        text = ctx.transcript.get_text()
    else:
        text = ctx.transcript.get_last_assistant()
        if not text:
            text = ctx.transcript.get_text()
    if not text:
        ctx.transcript.append("[info] transcript is empty")
        return
    ctx.app.copy_to_clipboard(text)
    ctx.transcript.append(f"[info] copied {len(text.splitlines())} line(s)")


def _cmd_help(ctx: CommandContext) -> None:
    ctx.transcript.append("[info] commands:")
    ctx.transcript.append("  /new, /clear        start a fresh thread")
    ctx.transcript.append("  /threads            open threads")
    ctx.transcript.append("  /switch <id>        resume existing thread")
    ctx.transcript.append("  /agents             browse agents")
    ctx.transcript.append("  /artifacts          open artifacts")
    ctx.transcript.append("  /runs               recent runs")
    ctx.transcript.append("  /rename <name>      rename current thread")
    ctx.transcript.append("  /copy               copy last message")
    ctx.transcript.append("  /copy all           copy full transcript")
    ctx.transcript.append("  /quit, /exit        leave the REPL")
    ctx.transcript.append("[info] keys: ctrl+q quit · ctrl+x cancel · tab navigate")
    if ctx.server_slash_commands:
        ctx.transcript.append("[info] server commands:")
        for cmd in ctx.server_slash_commands[:20]:
            ctx.transcript.append(f"  {cmd} <task>")
