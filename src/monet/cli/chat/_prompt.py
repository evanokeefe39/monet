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
        dock: bottom;
        height: 3;
        max-height: 8;
        border: solid $primary;
        padding: 0 1;
        margin-bottom: 1;
        background: black;
    }
    AutoGrowTextArea:focus {
        border: solid $accent;
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
        line_count = self.document.line_count
        target = max(self.MIN_HEIGHT, min(line_count + 1, self.MAX_HEIGHT))
        self.styles.height = target
