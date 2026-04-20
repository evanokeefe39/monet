"""Welcome overlay for the monet chat TUI.

Simple centered logo + command cheatsheet. Any key dismisses via
WelcomeDismissed message. No animation, no plasma.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.events import Key

from monet.cli.chat._constants import WELCOME_COMMANDS, WELCOME_LOGO


class WelcomeOverlay(Widget):
    """Centred logo + command cheatsheet. Any key dismisses."""

    can_focus = True

    DEFAULT_CSS = """
    WelcomeOverlay {
        layer: overlay;
        width: 100%;
        height: 100%;
        display: none;
        background: $surface 80%;
        align: center middle;
    }

    WelcomeOverlay.visible {
        display: block;
    }

    WelcomeOverlay #welcome-card {
        width: auto;
        height: auto;
        padding: 1 3;
        text-align: center;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="welcome-card")

    @staticmethod
    def _build_content() -> Group:
        logo = Text("\n".join(WELCOME_LOGO), style="bold #00c8da")
        tagline = Text("multi-agent orchestration", style="italic dim")
        width = max((len(cmd) for cmd, _ in WELCOME_COMMANDS), default=0)
        cmds = Text()
        for idx, (cmd, desc) in enumerate(WELCOME_COMMANDS):
            cmds.append(f"{cmd:<{width}}", style="bold #46b2e4")
            cmds.append(f"   {desc}", style="dim")
            if idx != len(WELCOME_COMMANDS) - 1:
                cmds.append("\n")
        hint = Text("press any key to start", style="italic #7a7a85")
        return Group(logo, tagline, Text(""), cmds, Text(""), hint)

    def on_key(self, event: Key) -> None:
        self.hide()
        dismiss = getattr(self.app, "dismiss_welcome", None)
        if dismiss is not None:
            dismiss()
        event.stop()

    def show(self) -> None:
        self.add_class("visible")
        self.focus()

    def hide(self) -> None:
        self.remove_class("visible")
