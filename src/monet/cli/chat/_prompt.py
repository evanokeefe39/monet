"""Auto-growing TextArea prompt for the monet chat TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding, BindingType
from textual.widgets import TextArea

if TYPE_CHECKING:
    from textual.events import Key

from monet.cli.chat._messages import PromptSubmitted


class AutoGrowTextArea(TextArea):
    """TextArea that grows vertically with content, up to MAX_HEIGHT."""

    MIN_HEIGHT = 3
    MAX_HEIGHT = 8

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "submit_prompt", "Submit", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    AutoGrowTextArea {
        width: 1fr;
        height: 100%;
        border: none;
        padding: 0 1;
        background: black;
    }
    AutoGrowTextArea:focus {
        border: none;
    }
    """

    def __init__(self, *, id: str = "prompt") -> None:
        super().__init__(id=id, language=None, soft_wrap=True)
        self.show_line_numbers = False

    def action_submit_prompt(self) -> None:
        text = self.text.strip()
        if text:
            self.clear()
            self.post_message(PromptSubmitted(text))

    def on_key(self, event: Key) -> None:
        if event.key == "shift+enter":
            event.prevent_default()
            self.insert("\n")

    def _on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Force a visual refresh on every content change. Without this, paste
        # (bracketed paste, no terminal key event) won't redraw when the
        # wrapped line count stays the same and virtual_size doesn't change.
        self.refresh(layout=True)
