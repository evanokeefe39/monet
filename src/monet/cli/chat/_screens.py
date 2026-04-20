"""Full-screen views for the monet chat TUI.

Each screen uses DataTable for tabular data and dismisses on Esc.
Push via app.push_screen(), result returned via dismiss().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Header, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult


_log = logging.getLogger("monet.cli.chat")

SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+Q", "Quit"),
    ("Ctrl+X", "Cancel in-flight run"),
    ("Ctrl+Space", "Focus prompt"),
    ("Ctrl+Tab", "Cycle focus"),
    ("Ctrl+1", "Threads"),
    ("Ctrl+2", "Agents"),
    ("Ctrl+3", "Artifacts"),
    ("Ctrl+4", "Runs"),
    ("Ctrl+P", "Menu"),
    ("Ctrl+K", "Shortcuts"),
    ("Enter", "Send / submit / confirm"),
    ("Shift+Enter", "Newline in input"),
    ("Esc", "Back / close"),
    ("Tab", "Accept completion"),
)


class _TableScreen(Screen[str | None]):
    """Base for DataTable-driven screens."""

    BINDINGS: ClassVar = [
        Binding("escape", "dismiss_screen", "Back", show=False),
    ]

    DEFAULT_CSS = """
    _TableScreen { align: center middle; }
    _TableScreen DataTable { height: 1fr; }
    _TableScreen #nav-hint {
        dock: bottom; height: 1;
        padding: 0 1; color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="table")
        yield Static("esc to go back · enter to select", id="nav-hint")

    def on_mount(self) -> None:
        self.run_worker(self._load_data())

    async def _load_data(self) -> None:
        raise NotImplementedError

    def _row_value(self, row_key: Any) -> str | None:
        table = self.query_one("#table", DataTable)
        try:
            row = table.get_row(row_key)
            return str(row[0]) if row else None
        except Exception:
            return None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        value = self._row_value(event.row_key)
        self.dismiss(value)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)


class ThreadsScreen(_TableScreen):
    """Thread browser. Returns selected thread_id or None."""

    def __init__(self, client: Any, thread_id: str = "") -> None:
        super().__init__()
        self._client = client
        self._current = thread_id

    async def _load_data(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("Thread ID", "Name", "Messages")
        try:
            chats = await self._client.chat.list_chats()
        except Exception as exc:
            _log.warning("threads load failed: %s", exc)
            return
        for c in chats:
            marker = " →" if c.thread_id == self._current else ""
            table.add_row(
                c.thread_id,
                (c.name or "(unnamed)") + marker,
                str(c.message_count),
                key=c.thread_id,
            )


class AgentsScreen(_TableScreen):
    """Agent capabilities browser. Returns selected slash command or None."""

    def __init__(self, client: Any) -> None:
        super().__init__()
        self._client = client

    async def _load_data(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("Command", "Description")
        try:
            caps = await self._client.list_capabilities()
        except Exception as exc:
            _log.warning("agents load failed: %s", exc)
            return
        for cap in sorted(caps, key=lambda c: (c.agent_id, c.command)):
            cmd = f"/{cap.agent_id}:{cap.command}"
            table.add_row(cmd, cap.description or "", key=cmd)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#table", DataTable)
        try:
            row = table.get_row(event.row_key)
            self.dismiss(str(row[0]) if row else None)
        except Exception:
            self.dismiss(None)


class ArtifactsScreen(_TableScreen):
    """Artifacts browser. Returns selected artifact_id or None."""

    _client: Any
    _tid: str

    def __init__(self, client: Any, thread_id: str) -> None:
        super().__init__()
        self._client = client
        self._tid = thread_id

    async def _load_data(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("Artifact ID", "Key", "Kind")
        if not self._tid:
            return
        try:
            artifacts = await self._client.list_artifacts(thread_id=self._tid)
        except Exception as exc:
            _log.warning("artifacts load failed: %s", exc)
            return
        for a in artifacts:
            table.add_row(
                a.artifact_id[:12],
                a.key or "",
                a.kind or "",
                key=a.artifact_id,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value) if event.row_key else None)


class RunsScreen(_TableScreen):
    """Recent runs browser. Returns selected run_id or None."""

    _client: Any
    _tid: str

    def __init__(self, client: Any, thread_id: str) -> None:
        super().__init__()
        self._client = client
        self._tid = thread_id

    async def _load_data(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("Run ID", "Status", "Created")
        try:
            runs = await self._client.list_runs(thread_id=self._tid, limit=20)
        except Exception as exc:
            _log.warning("runs load failed: %s", exc)
            return
        for r in runs:
            table.add_row(
                r.run_id[:12],
                r.status or "unknown",
                str(r.created_at or ""),
                key=r.run_id,
            )


class ShortcutsScreen(Screen[None]):
    """Keyboard shortcut reference."""

    BINDINGS: ClassVar = [
        Binding("escape", "app.pop_screen", "Back", show=False),
        Binding("ctrl+k", "app.pop_screen", "Back", show=False),
    ]

    DEFAULT_CSS = """
    ShortcutsScreen { align: center middle; }
    ShortcutsScreen #sc-body { width: 100%; height: auto; padding: 1 2; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        width = max(len(k) for k, _ in SHORTCUTS)
        body = Text()
        for i, (key, desc) in enumerate(SHORTCUTS):
            body.append(f"{key:<{width}}", style="bold $primary")
            body.append(f"   {desc}")
            if i < len(SHORTCUTS) - 1:
                body.append("\n")
        yield Static(body, id="sc-body")
        yield Static("esc / ctrl+k to close", id="nav-hint")


class ConfirmScreen(Screen[bool]):
    """Simple y/n confirmation dialog."""

    BINDINGS: ClassVar = [
        Binding("y", "confirm", show=False),
        Binding("n", "deny", show=False),
        Binding("escape", "deny", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen #confirm-body { padding: 2 4; border: solid $primary; }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Static(f"{self._message}\n\n[y] yes  [n] no", id="confirm-body")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)
