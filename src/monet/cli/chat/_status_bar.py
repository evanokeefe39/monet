"""Single-line status bar for the monet chat TUI."""

from __future__ import annotations

import time
from enum import Enum
from typing import TYPE_CHECKING

from rich.text import Text
from textual.widget import Widget

from monet.cli.chat._constants import SPINNER_INTERVAL

if TYPE_CHECKING:
    from textual.timer import Timer


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
        self._runs: int = 0
        self._active_run: str = ""
        self._focus: FocusMode = FocusMode.INPUT
        self._override_text: str = ""
        self._spinner_frame: int = 0
        self._spinner_timer: Timer | None = None
        self._run_start: float = 0.0

    def _tick_spinner(self) -> None:
        if not self._active_run or self._override_text:
            return
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
        runs: int | None = None,
        active_run: str | None = None,
    ) -> None:
        if thread_name is not None:
            self._thread_name = thread_name
        if agents is not None:
            self._agents = agents
        if artifacts is not None:
            self._artifacts = artifacts
        if runs is not None:
            self._runs = runs
        if active_run is not None:
            was_active = bool(self._active_run)
            self._active_run = active_run
            if active_run and not was_active:
                self._run_start = time.monotonic()
                self._spinner_timer = self.set_interval(
                    SPINNER_INTERVAL, self._tick_spinner
                )
            elif not active_run and was_active and self._spinner_timer is not None:
                self._spinner_timer.stop()
                self._spinner_timer = None
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
            elapsed = int(time.monotonic() - self._run_start)
            mins, secs = divmod(elapsed, 60)
            t.append(f"{mins}:{secs:02d} ", style="#7a7a85")
            t.append("run:", style="#168b9f")
            t.append(self._active_run[:8], style="#e0e0e8")
            t.append(" ", style="")
            t.append(self.update_spinner(), style="#00c8da")
            t.append(" · ", style="#7a7a85")

        if self._thread_name:
            t.append("thread:", style="#168b9f")
            t.append(self._thread_name, style="#e0e0e8")
            t.append(" · ", style="#7a7a85")

        t.append("runs:", style="#168b9f")
        t.append(str(self._runs), style="#e0e0e8")
        t.append(" · ", style="#7a7a85")

        t.append("agents:", style="#168b9f")
        t.append(str(self._agents), style="#e0e0e8")
        t.append(" · ", style="#7a7a85")

        t.append("artifacts:", style="#168b9f")
        t.append(str(self._artifacts), style="#e0e0e8")

        return t

    def update_spinner(self) -> str:
        """Return the current spinner frame."""
        return self._SPINNER_FRAMES[self._spinner_frame]
