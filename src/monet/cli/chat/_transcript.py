"""Transcript widget for the monet chat TUI.

VerticalScroll of MessageBlock widgets. Three content types:
1. Plain role-tagged lines (user, info, progress, error)
2. Markdown-rendered assistant messages
3. Inline HITL widgets mounted at the scroll bottom
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget

from monet.cli.chat._message_block import MessageBlock
from monet.cli.chat._welcome import WelcomeOverlay

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from monet.cli.chat._messages import TranscriptAppend

_log = logging.getLogger("monet.cli.chat")


class Transcript(Widget):
    """Scrollable conversation transcript with markdown and inline HITL support."""

    DEFAULT_CSS = """
    Transcript {
        height: 1fr;
        layers: base overlay;
    }

    Transcript #_scroll {
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

    def __init__(self, *, id: str = "transcript") -> None:
        super().__init__(id=id)
        self._lines: list[str] = []
        self._hitl_widget: Widget | None = None
        self._welcome_hidden: bool = False
        self._scroll_deferred: bool = False
        self._pending_scroll: bool = False

    def compose(self) -> ComposeResult:
        yield WelcomeOverlay(id="welcome")
        yield VerticalScroll(id="_scroll")
        yield Vertical(id="_hitl-area")

    def _scroll_view(self) -> VerticalScroll:
        return self.query_one("#_scroll", VerticalScroll)

    def append(self, line: str, *, markdown: bool = False, scroll: bool = True) -> None:
        """Append a line to the transcript."""
        self._lines.append(line)
        self._hide_welcome()
        block = MessageBlock(line, markdown=markdown)
        self._scroll_view().mount(block)
        if scroll:
            if self._scroll_deferred:
                self._pending_scroll = True
            else:
                self.call_after_refresh(self._scroll_view().scroll_end, animate=False)

    def defer_scroll(self) -> None:
        """Defer scrolling until flush_scroll is called."""
        self._scroll_deferred = True
        self._pending_scroll = False

    def flush_scroll(self) -> None:
        """Flush any pending scroll and resume per-call scrolling."""
        self._scroll_deferred = False
        if self._pending_scroll:
            self._pending_scroll = False
            self.call_after_refresh(self._scroll_view().scroll_end, animate=False)

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
        self._scroll_view().remove_children()

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
            block = MessageBlock(line, markdown=(role == "assistant"))
            self._scroll_view().mount(block)
        self._welcome_hidden = True
        self.call_after_refresh(self._scroll_view().scroll_end, animate=False)

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
