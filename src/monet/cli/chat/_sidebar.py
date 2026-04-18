"""Right-docked single-picker panel for the monet chat TUI.

Invoked from a slash command (``/agents``, ``/threads``, ``/artifacts``)
when the terminal is wide enough. Below :data:`BREAKPOINT_COLS` the app
pushes the full-screen :class:`~monet.cli.chat._pickers.PickerScreen`
instead. The panel hosts one :class:`OptionList` (no tabs) with a
title; populate is kind-specific and runs as a worker on mount.

Widget boundary: the panel is display-only. It receives callbacks
(``on_select``, ``on_close``) from :class:`ChatApp` and never reaches
back into app state.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from monet.client import MonetClient


#: Minimum terminal width (columns) for the docked sidebar. Below this
#: the app pushes a fullscreen picker instead.
BREAKPOINT_COLS = 80

#: Hard floor — at or below this width the sidebar refuses to open and
#: the app pushes a fullscreen picker even though the sidebar would
#: normally fit.
FLOOR_COLS = 50

SidebarKind = Literal["agents", "threads", "artifacts"]

# Back-compat alias — several modules still import SidebarTab.
SidebarTab = SidebarKind

_TITLES: dict[str, str] = {
    "agents": "Agents",
    "threads": "Threads",
    "artifacts": "Artifacts",
}


class SidebarPanel(Vertical):
    """Docked-right single-picker panel."""

    DEFAULT_CSS = """
    SidebarPanel {
        dock: right;
        width: 42;
        background: $panel;
        border-left: solid $panel-lighten-2;
        padding: 0 1;
    }

    SidebarPanel .sidebar-title {
        text-style: bold;
        color: $primary;
        padding: 0 0 1 0;
    }

    SidebarPanel OptionList {
        background: transparent;
        border: none;
        padding: 0;
        scrollbar-size-vertical: 1;
        height: 1fr;
    }

    SidebarPanel .sidebar-hint {
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS: ClassVar = [
        Binding("escape", "close", "Close", show=False),
        Binding("f", "fullscreen", "Fullscreen", show=False),
    ]

    def __init__(
        self,
        *,
        kind: SidebarKind,
        client: MonetClient,
        thread_id_getter: Any,
        on_select: Any,
        on_close: Any,
        on_fullscreen: Any,
    ) -> None:
        super().__init__(id="sidebar")
        self._kind: SidebarKind = kind
        self._client = client
        self._thread_id_getter = thread_id_getter
        self._on_select = on_select
        self._on_close = on_close
        self._on_fullscreen = on_fullscreen

    @property
    def kind(self) -> SidebarKind:
        return self._kind

    def compose(self) -> ComposeResult:
        yield Static(_TITLES[self._kind], classes="sidebar-title")
        yield OptionList(id="sidebar-list")
        yield Static("enter select · f fullscreen · esc close", classes="sidebar-hint")

    def on_mount(self) -> None:
        self.refresh_data()
        with contextlib.suppress(Exception):
            self.query_one("#sidebar-list", OptionList).focus()

    def refresh_data(self) -> None:
        if self._kind == "agents":
            self.run_worker(self._refill_agents(), exclusive=True, group="sidebar")
        elif self._kind == "threads":
            self.run_worker(self._refill_threads(), exclusive=True, group="sidebar")
        elif self._kind == "artifacts":
            self.run_worker(self._refill_artifacts(), exclusive=True, group="sidebar")

    async def _refill_agents(self) -> None:
        try:
            caps = await self._client.list_capabilities()
        except Exception:
            caps = []
        options: list[Option] = []
        for cap in sorted(
            caps, key=lambda c: (c.get("agent_id") or "", c.get("command") or "")
        ):
            agent_id = str(cap.get("agent_id") or "")
            command = str(cap.get("command") or "")
            desc = str(cap.get("description") or "").strip()
            if not agent_id or not command:
                continue
            value = f"/{agent_id}:{command}"
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append(value, style="bold")
            if desc:
                label.append(f"\n{desc}", style="dim")
            options.append(Option(label, id=value))
        self._apply_options(options, empty="no agents registered")

    async def _refill_threads(self) -> None:
        try:
            chats = await self._client.chat.list_chats()
        except Exception:
            chats = []
        current = self._thread_id_getter() or ""
        options: list[Option] = []
        for c in chats:
            marker = "● " if c.thread_id == current else "  "
            name = c.name or "(unnamed)"
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append(f"{marker}{name}", style="bold")
            label.append(f"\n{c.message_count} msgs · {c.thread_id[:8]}", style="dim")
            options.append(Option(label, id=c.thread_id))
        self._apply_options(options, empty="no chat threads yet")

    async def _refill_artifacts(self) -> None:
        thread_id = self._thread_id_getter() or ""
        rows: list[Any] = []
        if thread_id:
            try:
                from monet.core.artifacts import get_artifacts

                rows = list(
                    await get_artifacts().query_recent(thread_id=thread_id, limit=50)
                )
            except Exception:
                rows = []
        options: list[Option] = []
        for row in rows:
            art_id = str(getattr(row, "artifact_id", "") or getattr(row, "id", ""))
            kind = str(getattr(row, "kind", "") or "")
            key = str(getattr(row, "key", "") or "")
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append(key or kind or art_id[:8], style="bold")
            if kind and key:
                label.append(f"\n{kind}", style="dim")
            options.append(Option(label, id=art_id))
        self._apply_options(options, empty="no artifacts in this thread")

    def _apply_options(self, options: list[Option], *, empty: str) -> None:
        with contextlib.suppress(Exception):
            lst = self.query_one("#sidebar-list", OptionList)
            lst.clear_options()
            if not options and empty:
                placeholder = Text(empty, style="dim italic")
                lst.add_option(Option(placeholder, id="__empty__", disabled=True))
                return
            for opt in options:
                lst.add_option(opt)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        value = str(event.option.id or "")
        if value == "__empty__":
            return
        event.stop()
        self._on_select(self._kind, value)

    def action_close(self) -> None:
        self._on_close()

    def action_fullscreen(self) -> None:
        self._on_fullscreen(self._kind)
