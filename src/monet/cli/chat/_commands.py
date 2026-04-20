"""Slash command dispatch for the monet chat TUI.

Each TUI-local command is a small async function. The dispatcher checks
whether the text matches a local command; if not, it falls through so
the text is sent to the server as a chat message.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet.cli.chat._transcript import Transcript
    from monet.client import MonetClient

_log = logging.getLogger("monet.cli.chat")


class CommandContext:
    """Minimal context passed to each slash command handler."""

    def __init__(
        self,
        *,
        client: MonetClient,
        transcript: Transcript,
        thread_id: str,
        server_slash_commands: list[str],
        get_thread_id: Any,
        set_thread_id: Any,
        set_title: Any,
        update_status: Any,
        show_welcome: Any,
    ) -> None:
        self.client = client
        self.transcript = transcript
        self.thread_id = thread_id
        self.server_slash_commands = server_slash_commands
        self._get_thread_id = get_thread_id
        self._set_thread_id = set_thread_id
        self._set_title = set_title
        self._update_status = update_status
        self._show_welcome = show_welcome


async def dispatch_slash(ctx: CommandContext, text: str) -> bool:
    """Dispatch a TUI-local slash command. Returns True if handled."""
    head, _, rest = text.partition(" ")
    arg = rest.strip()

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
        _cmd_copy(ctx)
        return True
    if head == "/help":
        _cmd_help(ctx)
        return True
    if head in {"/threads", "/agents", "/artifacts", "/runs"}:
        # These are handled at the app level via screen push
        return False
    if head == "/colors":
        _cmd_colors(ctx, arg)
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
    ctx._set_thread_id(new_id)
    ctx._set_title(f"monet chat · {name}")
    ctx._update_status(thread_name=name)
    ctx.transcript.clear()
    ctx.transcript.append(f"[info] new thread · {name} · {new_id[:8]}")
    ctx._show_welcome()


async def _cmd_switch(ctx: CommandContext, target: str) -> None:
    if not target:
        ctx.transcript.append("[info] usage: /switch <thread_id>")
        return
    try:
        history = await ctx.client.chat.get_chat_history(target)
    except Exception as exc:
        ctx.transcript.append(f"[error] /switch failed: {exc}")
        return
    ctx._set_thread_id(target)
    ctx._set_title(f"monet chat · {target}")
    ctx._update_status(thread_name="")
    ctx.transcript.clear()
    ctx.transcript.append(f"[info] switched to {target}")
    for msg in history:
        role = str(msg.get("role") or "user")
        content = str(msg.get("content") or "")
        ctx.transcript.append(f"[{role}] {content}", markdown=(role == "assistant"))
    if not history:
        ctx._show_welcome()


async def _cmd_rename(ctx: CommandContext, name: str) -> None:
    thread_id = ctx._get_thread_id()
    if not thread_id:
        ctx.transcript.append("[error] no active thread to rename")
        return
    try:
        await ctx.client.chat.rename_chat(thread_id, name)
    except Exception as exc:
        ctx.transcript.append(f"[error] rename failed: {exc}")
        return
    ctx.transcript.append(f"[info] thread renamed to {name}")
    ctx._update_status(thread_name=name)


def _cmd_copy(ctx: CommandContext) -> None:
    text = ctx.transcript.get_text()
    if not text:
        ctx.transcript.append("[info] transcript is empty")
        return
    ctx.transcript.append(f"[info] copied {len(text.splitlines())} line(s)")


def _cmd_colors(ctx: CommandContext, arg: str) -> None:
    from textual.color import Color

    from monet.cli.chat._view import ROLE_TAGS

    parts = arg.split() if arg else []
    if not parts:
        ctx.transcript.append("[info] current colors (session):")
        for target, tag in ROLE_TAGS.items():
            style = ctx.transcript._tag_styles.get(tag, "")
            ctx.transcript.append(f"  {target:<11} {style}")
        ctx.transcript.append(
            "[info] set via: /colors <target> <colour> | /colors reset"
        )
        return
    if parts == ["reset"]:
        ctx.transcript.append("[info] colors reset to defaults")
        return
    if len(parts) != 2:
        ctx.transcript.append(
            "[error] usage: /colors | /colors reset | /colors <target> <colour>"
        )
        return
    target, value = parts[0].lower(), parts[1]
    try:
        Color.parse(value)
    except Exception:
        ctx.transcript.append(f"[error] '{value}' is not a valid colour")
        return
    role_tag = ROLE_TAGS.get(target)
    if role_tag is None:
        known = ", ".join(ROLE_TAGS.keys())
        ctx.transcript.append(f"[error] unknown target '{target}' (try: {known})")
        return
    existing = ctx.transcript._tag_styles.get(role_tag, "")
    modifier = "bold " if "bold" in existing.split() else ""
    ctx.transcript._tag_styles[role_tag] = f"{modifier}{value}".strip()
    ctx.transcript.append(f"[info] [{target}] colour set to {value}")


def _cmd_help(ctx: CommandContext) -> None:
    ctx.transcript.append("[info] commands:")
    ctx.transcript.append("  /new, /clear        start a fresh thread")
    ctx.transcript.append("  /threads            open threads            (ctrl+1)")
    ctx.transcript.append("  /switch <id>        resume existing thread")
    ctx.transcript.append("  /agents             browse agents           (ctrl+2)")
    ctx.transcript.append("  /artifacts          open artifacts          (ctrl+3)")
    ctx.transcript.append("  /runs               recent runs             (ctrl+4)")
    ctx.transcript.append("  /rename <name>      rename current thread")
    ctx.transcript.append("  /copy               copy transcript")
    ctx.transcript.append("  /colors             show/change palette")
    ctx.transcript.append("  /quit, /exit        leave the REPL")
    ctx.transcript.append("[info] shortcuts: ctrl+k to see all")
    if ctx.server_slash_commands:
        ctx.transcript.append("[info] server commands:")
        for cmd in ctx.server_slash_commands[:20]:
            ctx.transcript.append(f"  {cmd} <task>")
