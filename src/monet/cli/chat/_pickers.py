"""Full-screen list picker and confirm modal for the monet chat TUI."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, ClassVar

from textual.binding import Binding
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from textual.app import ComposeResult


class ConfirmScreen(ModalScreen[bool]):
    """Two-key confirm modal.  ``y`` → True, ``n`` / Esc → False."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen #dialog {
        background: $panel;
        border: round $primary;
        padding: 1 3;
        width: auto;
        max-width: 60;
        height: auto;
    }
    ConfirmScreen .confirm-msg {
        text-align: center;
        padding-bottom: 1;
    }
    ConfirmScreen .confirm-hint {
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="dialog"):
            yield Static(self._message, classes="confirm-msg")
            yield Static("y  confirm  ·  n / esc  cancel", classes="confirm-hint")

    def on_key(self, event: Any) -> None:
        if event.key == "y":
            self.dismiss(True)
        elif event.key in ("n", "escape"):
            self.dismiss(False)


class TablePickerScreen(Screen[str | None]):
    """Full-screen tabular picker — arrow keys nav, Enter select, Esc back.

    ``rows`` is a list of tuples where ``rows[i][0]`` is the row key
    (returned on selection) and ``rows[i][1:]`` are the display columns.

    Pass ``on_delete`` to enable ``del`` key deletion with a confirm
    modal. The callable receives the row key and should return ``True``
    on success (the row is then removed from the table).
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Back", show=False),
        Binding("shift+tab", "cancel", "Back", show=False),
        Binding("delete", "delete_row", "Delete", show=False),
    ]

    DEFAULT_CSS = """
    TablePickerScreen {
        padding: 1 2;
    }

    TablePickerScreen .picker-title {
        text-style: bold;
        padding-bottom: 1;
    }

    TablePickerScreen DataTable {
        height: 1fr;
        border: round $primary;
    }

    TablePickerScreen .picker-hint {
        color: $text-muted;
        padding-top: 1;
    }
    """

    def __init__(
        self,
        title: str,
        columns: list[str],
        rows: list[tuple[str, ...]],
        on_delete: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        """``rows[i][0]`` is the selection key; ``rows[i][1:]`` are display columns."""
        super().__init__()
        self._picker_title = title
        self._columns = columns
        self._rows = rows
        self._on_delete = on_delete
        self._highlighted_key: str = ""

    def compose(self) -> ComposeResult:
        yield Static(self._picker_title, classes="picker-title")
        yield DataTable(
            id="picker-table",
            cursor_type="row",
            show_cursor=True,
            zebra_stripes=True,
        )
        hint = "↑/↓ navigate · enter select · esc back"
        if self._on_delete is not None:
            hint += " · del delete"
        yield Static(hint, classes="picker-hint")

    def on_mount(self) -> None:
        table = self.query_one("#picker-table", DataTable)
        for col in self._columns:
            table.add_column(col)
        for row in self._rows:
            key = row[0]
            cells = row[1:]
            table.add_row(*cells, key=key)
        if not self._rows:
            table.add_column("(empty)")
            table.add_row("no items", key="__empty__")
        with contextlib.suppress(Exception):
            table.focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._highlighted_key = str(event.row_key.value or "")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value or "")
        if key == "__empty__":
            return
        self.dismiss(key)

    def action_delete_row(self) -> None:
        key = self._highlighted_key
        if not key or key == "__empty__" or self._on_delete is None:
            return
        # ``push_screen_wait`` requires a worker context (Textual ≥0.60).
        self.app.run_worker(self._delete_flow(key), exclusive=False)

    async def _delete_flow(self, key: str) -> None:
        if self._on_delete is None:
            return
        table = self.query_one("#picker-table", DataTable)
        try:
            row_data = table.get_row(key)
            label = str(row_data[0]) if row_data else key
        except Exception:
            label = key
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(f"Delete  {label!r}?")
        )
        if confirmed:
            success = await self._on_delete(key)
            if success:
                with contextlib.suppress(Exception):
                    table.remove_row(key)

    def action_cancel(self) -> None:
        self.dismiss(None)
