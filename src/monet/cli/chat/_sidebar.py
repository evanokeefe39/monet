"""Right-docked single-picker panel for the monet chat TUI.

Invoked from a slash command (``/agents``, ``/threads``, ``/artifacts``)
when the terminal is wide enough. Below :data:`BREAKPOINT_COLS` the app
pushes the full-screen :class:`~monet.cli.chat._pickers.TablePickerScreen`
instead. The panel hosts one :class:`DataTable` with kind-specific columns;
populate runs as a worker on mount.

Widget boundary: the panel is display-only. It receives callbacks
(``on_select``, ``on_close``, ``on_delete``) from :class:`ChatApp` and
never reaches back into app state.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Static

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

    SidebarPanel DataTable {
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
        Binding("e", "expand", "Expand", show=False),
        Binding("delete", "delete_row", "Delete", show=False),
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
        on_delete: Any = None,
    ) -> None:
        super().__init__(id="sidebar")
        self._kind: SidebarKind = kind
        self._client = client
        self._thread_id_getter = thread_id_getter
        self._on_select = on_select
        self._on_close = on_close
        self._on_fullscreen = on_fullscreen
        self._on_delete = on_delete
        self._highlighted_key: str = ""

    @property
    def kind(self) -> SidebarKind:
        return self._kind

    def compose(self) -> ComposeResult:
        yield Static(_TITLES[self._kind], classes="sidebar-title")
        yield DataTable(id="sidebar-table", cursor_type="row", show_cursor=True)
        hint = "enter select · e expand · esc close"
        if self._kind == "threads":
            hint += " · del delete"
        yield Static(hint, classes="sidebar-hint")

    def on_mount(self) -> None:
        self.refresh_data()
        with contextlib.suppress(Exception):
            self.query_one("#sidebar-table", DataTable).focus()

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
        table = self.query_one("#sidebar-table", DataTable)
        table.clear(columns=True)
        table.add_column("Command", key="cmd")
        table.add_column("Description", key="desc")
        sorted_caps = sorted(
            caps, key=lambda c: (c.get("agent_id") or "", c.get("command") or "")
        )
        added = 0
        for cap in sorted_caps:
            agent_id = str(cap.get("agent_id") or "")
            command = str(cap.get("command") or "")
            desc = str(cap.get("description") or "").strip()
            if not agent_id or not command:
                continue
            value = f"/{agent_id}:{command}"
            table.add_row(value, desc or "—", key=value)
            added += 1
        if not added:
            table.add_row("(no agents registered)", "—", key="__empty__")

    async def _refill_threads(self) -> None:
        try:
            chats = await self._client.chat.list_chats()
        except Exception:
            chats = []
        current = self._thread_id_getter() or ""
        table = self.query_one("#sidebar-table", DataTable)
        table.clear(columns=True)
        table.add_column("Name", key="name")
        table.add_column("Msgs", key="msgs")
        table.add_column("ID", key="tid")
        if not chats:
            table.add_row("(no threads yet)", "", "", key="__empty__")
            return
        for c in chats:
            marker = "▶ " if c.thread_id == current else ""
            name = marker + (c.name or "(unnamed)")
            table.add_row(name, str(c.message_count), c.thread_id[:8], key=c.thread_id)

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
        table = self.query_one("#sidebar-table", DataTable)
        table.clear(columns=True)
        table.add_column("Key", key="key")
        table.add_column("Kind", key="kind")
        table.add_column("ID", key="aid")
        if not rows:
            table.add_row("(no artifacts)", "", "", key="__empty__")
            return
        for row in rows:
            art_id = str(getattr(row, "artifact_id", "") or getattr(row, "id", ""))
            kind = str(getattr(row, "kind", "") or "—")
            key = str(getattr(row, "key", "") or "—")
            table.add_row(key, kind, art_id[:8], key=art_id)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._highlighted_key = str(event.row_key.value or "")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        value = str(event.row_key.value or "")
        if value == "__empty__":
            return
        event.stop()
        self._on_select(self._kind, value)

    def action_delete_row(self) -> None:
        if self._kind != "threads":
            return
        key = self._highlighted_key
        if not key or key == "__empty__" or self._on_delete is None:
            return
        # ``push_screen_wait`` requires a worker context (Textual ≥0.60).
        self.app.run_worker(self._delete_flow(key), exclusive=False)

    async def _delete_flow(self, key: str) -> None:
        table = self.query_one("#sidebar-table", DataTable)
        try:
            row_data = table.get_row(key)
            label = str(row_data[0]) if row_data else key
        except Exception:
            label = key
        from monet.cli.chat._pickers import ConfirmScreen

        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(f"Delete thread  {label!r}?")
        )
        if not confirmed:
            return
        try:
            await self._client.chat.delete_chat(key)
        except Exception as exc:
            self.app.notify(f"delete failed: {exc}", severity="error")
            return
        with contextlib.suppress(Exception):
            table.remove_row(key)
        if self._on_delete is not None:
            self._on_delete(self._kind, key)

    def action_close(self) -> None:
        self._on_close()

    def action_expand(self) -> None:
        self._on_fullscreen(self._kind)
