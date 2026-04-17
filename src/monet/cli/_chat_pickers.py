"""Full-screen list picker for the monet chat TUI."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding

if TYPE_CHECKING:
    from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class PickerScreen(Screen[str | None]):
    """Full-screen list picker — arrow keys nav, Enter select, Esc back."""

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Back", show=False),
        Binding("shift+tab", "cancel", "Back", show=False),
    ]

    DEFAULT_CSS = """
    PickerScreen {
        padding: 1 2;
    }

    PickerScreen .picker-title {
        text-style: bold;
        padding-bottom: 1;
    }

    PickerScreen OptionList {
        height: 1fr;
        border: round $primary;
    }

    PickerScreen .picker-hint {
        color: $text-muted;
        padding-top: 1;
    }
    """

    def __init__(self, title: str, options: list[tuple[str, str]]) -> None:
        """``options`` is ``[(value, display_label), …]``."""
        super().__init__()
        self._picker_title = title
        self._options = options

    def compose(self) -> ComposeResult:
        yield Static(self._picker_title, classes="picker-title")
        yield OptionList(
            *(Option(label, id=value) for value, label in self._options),
            id="picker",
        )
        yield Static(
            "↑/↓ navigate · enter select · esc back",
            classes="picker-hint",
        )

    def on_mount(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one(OptionList).focus()

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        self.dismiss(str(event.option.id) if event.option.id else None)

    def action_cancel(self) -> None:
        self.dismiss(None)
