"""MessageBlock: a single transcript message rendered as a Static widget."""

from __future__ import annotations

from rich.markdown import Markdown
from textual.widgets import Static

from monet.cli.chat._view import styled_line


class MessageBlock(Static):
    """Renders one transcript message — role-tagged plain line or markdown."""

    DEFAULT_CSS = """
    MessageBlock {
        height: auto;
        padding: 0 0;
    }
    """

    def __init__(self, line: str, *, markdown: bool = False) -> None:
        super().__init__()
        self._line = line
        self._markdown = markdown

    def on_mount(self) -> None:
        if self._markdown:
            content = self._line.removeprefix("[assistant] ")
            self.update(Markdown(content))
        else:
            self.update(styled_line(self._line))
