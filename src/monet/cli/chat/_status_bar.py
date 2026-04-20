"""Single-line status bar for the monet chat TUI."""

from __future__ import annotations

from enum import Enum

from rich.text import Text
from textual.widget import Widget


class FocusMode(Enum):
    INPUT = "input"
    TRANSCRIPT = "transcript"


class StatusBar(Widget):
    """Bottom status bar: focus glyph, thread name, counts, run spinner."""

    _SPINNER_FRAMES = (
        "▰▱▱▱▱▱▱",
        "▰▰▱▱▱▱▱",
        "▰▰▰▱▱▱▱",
        "▰▰▰▰▱▱▱",
        "▰▰▰▰▰▱▱",
        "▰▰▰▰▰▰▱",
        "▰▰▰▰▰▰▰",
        "▱▰▰▰▰▰▰",
        "▱▱▰▰▰▰▰",
        "▱▱▱▰▰▰▰",
        "▱▱▱▱▰▰▰",
        "▱▱▱▱▱▰▰",
        "▱▱▱▱▱▱▰",
        "▱▱▱▱▱▱▱",
    )

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: black;
        color: $text-muted;
    }
    """

    def __init__(self, *, id: str = "status-bar") -> None:
        super().__init__(id=id)
        self._thread_name: str = ""
        self._agents: int = 0
        self._artifacts: int = 0
        self._active_run: str = ""
        self._focus: FocusMode = FocusMode.INPUT
        self._override_text: str = ""
        self._spinner_frame: int = 0

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if self._active_run:
            self._spinner_frame = (self._spinner_frame + 1) % len(self._SPINNER_FRAMES)
            self.refresh()

    def set_override(self, text: str) -> None:
        self._override_text = text
        self.refresh()

    def clear_override(self) -> None:
        self._override_text = ""
        self.refresh()

    @property
    def has_override(self) -> bool:
        return bool(self._override_text)

    def set_focus(self, mode: FocusMode) -> None:
        self._focus = mode
        self.refresh()

    def update_segments(
        self,
        *,
        thread_name: str | None = None,
        agents: int | None = None,
        artifacts: int | None = None,
        active_run: str | None = None,
    ) -> None:
        if thread_name is not None:
            self._thread_name = thread_name
        if agents is not None:
            self._agents = agents
        if artifacts is not None:
            self._artifacts = artifacts
        if active_run is not None:
            self._active_run = active_run
        self.refresh()

    def render(self) -> Text:
        if self._override_text:
            return Text(self._override_text, overflow="ellipsis", no_wrap=True)
        return self._build_text()

    def _build_text(self) -> Text:
        t = Text(overflow="ellipsis", no_wrap=True)
        glyph_style = "bold #2d8db5" if self._focus == FocusMode.INPUT else "#7a7a85"
        t.append("▎ ", style=glyph_style)

        if self._active_run:
            t.append("run:", style="#168b9f")
            t.append(self._active_run[:8], style="#e0e0e8")
            t.append(" ", style="")
            t.append(self.update_spinner(), style="#00c8da")
            t.append(" · ", style="#7a7a85")

        if self._thread_name:
            t.append("thread:", style="#168b9f")
            t.append(self._thread_name, style="#e0e0e8")
            t.append(" · ", style="#7a7a85")

        t.append("agents:", style="#168b9f")
        t.append(str(self._agents), style="#e0e0e8")
        t.append(" · ", style="#7a7a85")

        t.append("artifacts:", style="#168b9f")
        t.append(str(self._artifacts), style="#e0e0e8")

        return t

    def _refresh(self) -> None:
        self.refresh()

    def update_spinner(self) -> str:
        """Return the current spinner frame."""
        return self._SPINNER_FRAMES[self._spinner_frame]
