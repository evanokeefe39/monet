"""Transcript widget for the monet chat TUI.

Wraps a RichLog and handles three content types:
1. Plain role-tagged lines (user, info, progress, error)
2. Markdown-rendered assistant messages
3. Inline HITL widgets mounted at the scroll bottom
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import RichLog

from monet.cli.chat._view import styled_line

if TYPE_CHECKING:
    from monet.cli.chat._messages import TranscriptAppend
from monet.cli.chat._welcome import WelcomeOverlay

if TYPE_CHECKING:
    from textual.app import ComposeResult


class Transcript(Widget):
    """Scrollable conversation transcript with markdown and inline HITL support."""

    DEFAULT_CSS = """
    Transcript {
        height: 1fr;
        layers: base overlay;
    }

    Transcript #_log {
        height: 1fr;
        border: none;
        padding: 0 1;
        background: $background;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
    }

    Transcript #_hitl-area {
        height: auto;
        dock: bottom;
    }
    """

    def __init__(
        self,
        *,
        tag_styles: dict[str, str] | None = None,
        id: str = "transcript",
    ) -> None:
        super().__init__(id=id)
        self._tag_styles = tag_styles or {}
        self._lines: list[str] = []
        self._hitl_widget: Widget | None = None

    def compose(self) -> ComposeResult:
        yield WelcomeOverlay(id="welcome")
        yield RichLog(id="_log", wrap=True, markup=False, highlight=False)
        yield Vertical(id="_hitl-area")

    def append(self, line: str, *, markdown: bool = False) -> None:
        """Append a line to the transcript."""
        self._lines.append(line)
        self._hide_welcome()
        log = self.query_one("#_log", RichLog)
        if markdown:
            content = line.removeprefix("[assistant] ")
            log.write(Markdown(content))
        else:
            log.write(styled_line(line, self._tag_styles))
        log.scroll_end(animate=False)

    def on_transcript_append(self, msg: TranscriptAppend) -> None:
        """Handle TranscriptAppend messages from workers."""
        self.append(msg.line, markdown=msg.markdown)
        msg.stop()

    def mount_hitl(self, widget: Widget) -> None:
        """Mount a HITL widget inline below the transcript."""
        self.unmount_hitl()
        area = self.query_one("#_hitl-area", Vertical)
        area.mount(widget)
        self._hitl_widget = widget

    def unmount_hitl(self) -> None:
        """Remove the current HITL widget if any."""
        if self._hitl_widget is not None:
            self._hitl_widget.remove()
            self._hitl_widget = None

    def clear(self) -> None:
        """Clear transcript content."""
        self._lines = []
        self.query_one("#_log", RichLog).clear()

    def get_text(self) -> str:
        """Plain text copy of all transcript lines."""
        return "\n".join(self._lines)

    def show_welcome(self) -> None:
        """Show the welcome overlay."""
        self.query_one("#welcome", WelcomeOverlay).show()

    def _hide_welcome(self) -> None:
        """Hide welcome overlay on first content."""
        try:
            w = self.query_one("#welcome", WelcomeOverlay)
            if w.has_class("visible"):
                w.hide()
        except Exception:
            pass

    def update_tag_styles(self, styles: dict[str, str]) -> None:
        """Update role tag colour overrides."""
        self._tag_styles = styles
