"""Main menu modal for the monet chat TUI (``ctrl+p``).

Replaces Textual's built-in ``CommandPalette`` with a monet-native
menu geared at the end-user experience rather than the developer
palette. Four sections — Keyboard Shortcuts, Options, Command
Library, About — plus an Exit action. Each section is a small
sub-screen pushed on top; escape pops back to the menu and escape
again closes the menu.

Widget boundary: the menu knows nothing about ``ChatApp`` specifics
beyond the data it is handed on construction, so it can be restyled
or extended without touching the rest of the app.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import ComposeResult


# ── Menu sections ────────────────────────────────────────────────

#: Top-level menu items. Order here is order on-screen.
MENU_KEYBOARD = "keyboard"
MENU_OPTIONS = "options"
MENU_LIBRARY = "library"
MENU_ABOUT = "about"
MENU_EXIT = "exit"


# ── Shared modal CSS ─────────────────────────────────────────────

#: Applied to every ``_MenuBase`` subclass so the menu + sub-screens
#: share one consistent game-menu look: centered, thin white border,
#: slightly brighter-than-background panel.
_MENU_CSS = """
_MenuBase {
    align: center middle;
    background: black 70%;
}

_MenuBase > Vertical {
    width: 60%;
    max-width: 70;
    height: auto;
    max-height: 85%;
    padding: 1 2;
    background: $boost;
    border: solid white;
}

_MenuBase .menu-title {
    text-style: bold;
    color: $primary;
    padding-bottom: 1;
}

_MenuBase .menu-hint {
    color: $text-muted;
    padding-top: 1;
}

_MenuBase OptionList {
    background: transparent;
    border: none;
    padding: 0;
    scrollbar-size-vertical: 1;
}
"""


class _MenuBase(ModalScreen[str | None]):
    """Base for the main menu and its sub-screens."""

    DEFAULT_CSS = _MENU_CSS
    BINDINGS: ClassVar = [
        Binding("escape", "dismiss_menu", "Back", show=False),
    ]

    def action_dismiss_menu(self) -> None:
        self.dismiss(None)


# ── Main menu ────────────────────────────────────────────────────


class MainMenuScreen(_MenuBase):
    """Top-level ``ctrl+p`` menu."""

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("monet chat menu", classes="menu-title")
            options: list[Option] = []
            for section_id, label, hint in (
                (MENU_KEYBOARD, "Keyboard Shortcuts", "view key bindings"),
                (MENU_OPTIONS, "Options", "theme, pulse, border"),
                (MENU_LIBRARY, "Command Library", "every slash command"),
                (MENU_ABOUT, "About", "version + links"),
                (MENU_EXIT, "Exit", "close monet chat"),
            ):
                text = Text()
                text.append(label, style="bold")
                text.append(f"\n{hint}", style="dim")
                options.append(Option(text, id=section_id))
            yield OptionList(*options, id="menu-root")
            yield Static("enter select · esc close", classes="menu-hint")

    def on_mount(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#menu-root", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(str(event.option.id or ""))


# ── Sub-screens ──────────────────────────────────────────────────


class KeyboardShortcutsScreen(_MenuBase):
    """Static cheatsheet of the chat keybindings."""

    _BINDINGS_LIST: ClassVar[tuple[tuple[str, str], ...]] = (
        ("ctrl+p", "open this menu"),
        ("ctrl+c x2", "quit (two presses)"),
        ("ctrl+q", "quit immediately"),
        ("tab", "accept slash completion"),
        ("↓ / ↑", "navigate suggestion dropdown"),
        ("esc", "close popups / sidebar"),
        ("enter", "send message · pick option"),
        ("f1", "toggle textual help panel"),
    )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Keyboard Shortcuts", classes="menu-title")
            options: list[Option] = []
            width = max(len(key) for key, _ in self._BINDINGS_LIST)
            for key, desc in self._BINDINGS_LIST:
                text = Text()
                text.append(f"{key:<{width}}", style="bold")
                text.append(f"   {desc}", style="dim")
                options.append(Option(text, id=f"kbd:{key}"))
            yield OptionList(*options, id="menu-keys")
            yield Static("esc to go back", classes="menu-hint")

    def on_mount(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#menu-keys", OptionList).focus()


class OptionsScreen(_MenuBase):
    """Themes + pulse toggle + border colour."""

    def __init__(
        self,
        *,
        current_theme: str,
        themes: tuple[str, ...],
        pulse_enabled: bool,
    ) -> None:
        super().__init__()
        self._current_theme = current_theme
        self._themes = themes
        self._pulse_enabled = pulse_enabled

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Options", classes="menu-title")
            options: list[Option] = []
            for theme in self._themes:
                marker = "● " if theme == self._current_theme else "  "
                text = Text()
                text.append(f"{marker}Theme · {theme}", style="bold")
                options.append(Option(text, id=f"theme:{theme}"))
            pulse_marker = "on" if self._pulse_enabled else "off"
            text = Text()
            text.append(f"  Pulse · {pulse_marker}", style="bold")
            text.append("\n  toggle the border breathing animation", style="dim")
            options.append(Option(text, id="pulse:toggle"))
            yield OptionList(*options, id="menu-options")
            yield Static("esc to go back", classes="menu-hint")

    def on_mount(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#menu-options", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(str(event.option.id or ""))


class CommandLibraryScreen(_MenuBase):
    """Every known slash command with its description."""

    def __init__(self, commands: list[tuple[str, str]]) -> None:
        """``commands`` is a pre-sorted list of ``(cmd, description)`` pairs."""
        super().__init__()
        self._commands = commands

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Command Library", classes="menu-title")
            options: list[Option] = []
            width = max((len(cmd) for cmd, _ in self._commands), default=0)
            for cmd, desc in self._commands:
                text = Text(no_wrap=True, overflow="ellipsis")
                text.append(f"{cmd:<{width}}", style="bold")
                if desc:
                    text.append(f"   {desc}", style="dim")
                options.append(Option(text, id=cmd))
            yield OptionList(*options, id="menu-library")
            yield Static("enter prefills prompt · esc to go back", classes="menu-hint")

    def on_mount(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#menu-library", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(str(event.option.id or ""))


class AboutScreen(_MenuBase):
    """Product info lines — version, repo, feedback."""

    _LINES: ClassVar[tuple[str, ...]] = (
        "monet — multi-agent orchestration platform",
        "",
        "repository: github.com/evanokeefe39/monet",
        "issues:     github.com/evanokeefe39/monet/issues",
        "",
        "chat UI runs against a local Aegra server via MonetClient.",
    )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("About", classes="menu-title")
            body = Text()
            for idx, line in enumerate(self._LINES):
                body.append(line)
                if idx != len(self._LINES) - 1:
                    body.append("\n")
            yield Static(body)
            yield Static("esc to go back", classes="menu-hint")


# Re-export so callers can import all screens from one place.
__all__ = [
    "MENU_ABOUT",
    "MENU_EXIT",
    "MENU_KEYBOARD",
    "MENU_LIBRARY",
    "MENU_OPTIONS",
    "AboutScreen",
    "CommandLibraryScreen",
    "KeyboardShortcutsScreen",
    "MainMenuScreen",
    "OptionsScreen",
]
