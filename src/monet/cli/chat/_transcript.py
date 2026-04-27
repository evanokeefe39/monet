"""Transcript widget for the monet chat TUI.

Wraps a RichLog and handles three content types:
1. Plain role-tagged lines (user, info, progress, error)
2. Markdown-rendered assistant messages
3. Inline HITL widgets mounted at the scroll bottom
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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

_log = logging.getLogger("monet.cli.chat")


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
        id: str = "transcript",
    ) -> None:
        super().__init__(id=id)
        self._lines: list[str] = []
        self._hitl_widget: Widget | None = None
        self._rich_log: RichLog | None = None
        self._welcome_hidden: bool = False
        self._scroll_deferred: bool = False
        self._pending_scroll: bool = False

    def on_mount(self) -> None:
        self._rich_log = self.query_one("#_log", RichLog)

    def compose(self) -> ComposeResult:
        yield WelcomeOverlay(id="welcome")
        yield RichLog(id="_log", wrap=True, markup=False, highlight=False)
        yield Vertical(id="_hitl-area")

    def append(self, line: str, *, markdown: bool = False, scroll: bool = True) -> None:
        """Append a line to the transcript."""
        self._lines.append(line)
        self._hide_welcome()
        rich_log = self._rich_log
        if rich_log is None:
            return
        if markdown:
            content = line.removeprefix("[assistant] ")
            rich_log.write(Markdown(content))
        else:
            rich_log.write(styled_line(line))
        if scroll:
            if self._scroll_deferred:
                self._pending_scroll = True
            else:
                rich_log.scroll_end(animate=False)

    def defer_scroll(self) -> None:
        """Defer scrolling until flush_scroll is called."""
        self._scroll_deferred = True
        self._pending_scroll = False

    def flush_scroll(self) -> None:
        """Flush any pending scroll and resume per-call scrolling."""
        self._scroll_deferred = False
        if self._pending_scroll and self._rich_log:
            self._pending_scroll = False
            self._rich_log.scroll_end(animate=False)

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

    def load_history(self, messages: list[Any]) -> None:
        _type_to_role = {"ai": "assistant", "human": "user", "system": "system"}
        for msg in messages:
            if isinstance(msg, dict):
                role = str(
                    msg.get("role")
                    or _type_to_role.get(str(msg.get("type") or ""), "user")
                )
                content = str(msg.get("content") or "")
            elif hasattr(msg, "content"):
                role = _type_to_role.get(getattr(msg, "type", ""), "user")
                content = str(msg.content or "")
            else:
                continue
            line = f"[{role}] {content}"
            self._lines.append(line)
            if self._rich_log is not None:
                if role == "assistant":
                    self._rich_log.write(Markdown(content))
                else:
                    self._rich_log.write(styled_line(line))
        self._welcome_hidden = True
        if self._rich_log is not None:
            self._rich_log.scroll_end(animate=False)

    def get_text(self) -> str:
        """Plain text copy of all transcript lines."""
        return "\n".join(self._lines)

    def get_last_assistant(self) -> str:
        """Return content of the last [assistant] message, or empty string."""
        for line in reversed(self._lines):
            if line.startswith("[assistant]"):
                return line.removeprefix("[assistant] ")
        return ""

    def show_welcome(self) -> None:
        """Show the welcome overlay."""
        self.query_one("#welcome", WelcomeOverlay).show()

    def _hide_welcome(self) -> None:
        """Hide welcome overlay on first content."""
        if self._welcome_hidden:
            return
        try:
            w = self.query_one("#welcome", WelcomeOverlay)
            if w.has_class("visible"):
                w.hide()
                self._welcome_hidden = True
        except Exception:
            _log.debug("hide welcome failed", exc_info=True)
