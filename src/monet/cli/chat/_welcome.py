"""Empty-state welcome overlay for the monet chat TUI.

Rendered centered inside the transcript region when no conversation
history exists. Shown on mount of a brand-new chat, after ``/new``, and
after ``/switch`` into an empty thread. Hidden on the first transcript
write so the conversation takes over cleanly.
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from monet.cli.chat._constants import WELCOME_COMMANDS, WELCOME_LOGO


class WelcomeOverlay(Static):
    """Centred logo + key-command cheatsheet, toggled via a CSS class."""

    DEFAULT_CSS = """
    WelcomeOverlay {
        layer: overlay;
        width: 100%;
        height: 100%;
        content-align: center middle;
        background: transparent;
        color: $text-muted;
        overflow: hidden;
        display: none;
    }

    WelcomeOverlay.visible {
        display: block;
    }
    """

    def __init__(self, *, id: str = "welcome") -> None:
        super().__init__(self._build_renderable(), id=id)

    @staticmethod
    def _build_renderable() -> Group:
        """Return the centred Rich ``Group`` (logo + command cheatsheet)."""
        logo = Text("\n".join(WELCOME_LOGO), style="bold magenta")
        width = max((len(cmd) for cmd, _ in WELCOME_COMMANDS), default=0)
        cmds = Text()
        for idx, (cmd, desc) in enumerate(WELCOME_COMMANDS):
            cmds.append(f"{cmd:<{width}}", style="bold cyan")
            cmds.append(f"   {desc}", style="dim")
            if idx != len(WELCOME_COMMANDS) - 1:
                cmds.append("\n")
        return Group(logo, Text(""), cmds)

    def show(self) -> None:
        """Re-render and make the overlay visible."""
        self.update(self._build_renderable())
        self.add_class("visible")

    def hide(self) -> None:
        """Hide the overlay. Idempotent."""
        self.remove_class("visible")
