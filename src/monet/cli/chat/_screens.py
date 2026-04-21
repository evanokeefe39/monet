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

from monet.cli._render import format_age

if TYPE_CHECKING:
    from textual.app import ComposeResult


_log = logging.getLogger("monet.cli.chat")

SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+Q", "Quit"),
    ("Ctrl+X", "Cancel run"),
    ("Tab", "Cycle suggestions / form fields"),
    ("Shift+Tab", "Cycle backward"),
    ("Enter", "Send / accept / submit"),
    ("Shift+Enter", "Newline in input"),
    ("Esc", "Close / back"),
    ("/threads", "Thread browser"),
    ("/agents", "Agent browser"),
    ("/artifacts", "Artifact browser"),
    ("/runs", "Run browser"),
    ("/copy", "Copy last message"),
    ("/copy all", "Copy full transcript"),
    ("/shortcuts", "This screen"),
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
        yield DataTable(id="table", cursor_type="row")
        yield Static("esc to go back · enter to select", id="nav-hint")

    def on_mount(self) -> None:
        self.query_one("#table", DataTable).focus()
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
        table.add_columns("Thread ID", "Name", "Messages", "Artifacts", "Last Active")
        try:
            chats = await self._client.chat.list_chats()
        except Exception as exc:
            _log.warning("threads load failed: %s", exc)
            return
        thread_ids = [c.thread_id for c in chats]
        try:
            artifact_counts: dict[
                str, int
            ] = await self._client.count_artifacts_per_thread(thread_ids)
        except Exception:
            artifact_counts = {}
        for c in chats:
            age = format_age(c.updated_at)
            marker = " →" if c.thread_id == self._current else ""
            table.add_row(
                c.thread_id,
                (c.name or "(unnamed)") + marker,
                str(c.message_count),
                str(artifact_counts.get(c.thread_id, 0)),
                age,
                key=c.thread_id,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(self._row_value(event.row_key))


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
        for cap in sorted(
            caps,
            key=lambda c: (c.get("agent_id", ""), c.get("command", "")),
        ):
            cmd = f"/{cap.get('agent_id')}:{cap.get('command')}"
            table.add_row(cmd, cap.get("description") or "", key=cmd)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#table", DataTable)
        try:
            row = table.get_row(event.row_key)
            self.dismiss(str(row[0]) if row else None)
        except Exception:
            self.dismiss(None)


class ArtifactsScreen(_TableScreen):
    """Artifacts browser. Enter opens the artifact view URL in the browser."""

    _client: Any
    _tid: str
    _base_url: str

    def __init__(self, client: Any, thread_id: str) -> None:
        super().__init__()
        self._client = client
        self._tid = thread_id
        self._base_url = str(getattr(client, "_url", "") or "http://localhost:2026")

    async def _load_data(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("ID", "Summary", "Type", "Agent", "View")
        if not self._tid:
            return
        try:
            artifacts = await self._client.list_artifacts(thread_id=self._tid)
        except Exception as exc:
            _log.warning("artifacts load failed: %s", exc)
            return
        base = self._base_url.rstrip("/")
        for a in artifacts:
            short_id = a.artifact_id[:8]
            kind = (a.kind or "").split("/")[-1].split(";")[0][:10]
            view_url = f"{base}/api/v1/artifacts/{a.artifact_id}/view"
            link = Text()
            link.append("↗ view", style=f"link {view_url} bold #00c8da")
            table.add_row(
                short_id,
                (a.summary or "")[:48],
                kind,
                (a.agent_id or "")[:16],
                link,
                key=a.artifact_id,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        import webbrowser

        artifact_id = str(event.row_key.value) if event.row_key else None
        if artifact_id:
            base = self._base_url.rstrip("/")
            webbrowser.open(f"{base}/api/v1/artifacts/{artifact_id}/view")
        self.dismiss(None)


class RunsScreen(_TableScreen):
    """Per-thread runs browser with interrupt→resume links."""

    _client: Any
    _tid: str

    def __init__(self, client: Any, thread_id: str) -> None:
        super().__init__()
        self._client = client
        self._tid = thread_id

    async def _load_data(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("Run ID", "Status", "Resumed By", "Created")
        if not self._tid:
            return
        try:
            runs = await self._client.chat.list_thread_runs(self._tid, limit=50)
        except Exception as exc:
            _log.warning("runs load failed: %s", exc)
            return
        status_styles: dict[str, str] = {
            "success": "#00c8da",
            "interrupted": "#e8a838",
            "error": "#e05050",
            "running": "#50b0e0",
        }
        for r in runs:
            status_text = Text(r.status or "unknown")
            style = status_styles.get(r.status, "#7a7a85")
            status_text.stylize(style)
            resumed = r.resumed_by[:8] if r.resumed_by else ""
            resumed_text = Text(resumed)
            if resumed:
                resumed_text.stylize("#00c8da")
            table.add_row(
                r.run_id[:12],
                status_text,
                resumed_text,
                format_age(r.created_at) if r.created_at else "",
                key=r.run_id,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(self._row_value(event.row_key))


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
