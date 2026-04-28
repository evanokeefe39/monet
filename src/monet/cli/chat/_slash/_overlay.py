"""SlashOverlay — floating autocomplete widget for slash commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.message import Message
from textual.widget import Widget
from textual.widgets import OptionList
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import ComposeResult

from monet.cli.chat._constants import SLASH_SUGGEST_MAX_OPTIONS


class SlashOverlay(Widget):
    """Floating slash-command autocomplete overlay.

    Precondition: mounted inside a container with ``layer: overlay`` so it
    floats above the prompt without displacing it.
    """

    class Accepted(Message):
        """Emitted when the user confirms a slash command from the overlay."""

        def __init__(self, command: str) -> None:
            super().__init__()
            self.command = command

    DEFAULT_CSS = """
    SlashOverlay > OptionList {
        border: none;
        height: auto;
        padding: 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield OptionList(id="slash-list")

    @property
    def _list(self) -> OptionList:
        return self.query_one("#slash-list", OptionList)

    # ── Public API ────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """True when overlay is shown and has at least one option."""
        return "visible" in self.classes and self._list.option_count > 0

    @property
    def option_count(self) -> int:
        return self._list.option_count

    @property
    def highlighted(self) -> int | None:
        return self._list.highlighted

    @highlighted.setter
    def highlighted(self, idx: int) -> None:
        self._list.highlighted = idx

    def hide(self) -> None:
        self.remove_class("visible")

    def cycle(self, delta: int) -> str | None:
        """Cycle highlight by *delta* steps. Returns the selected command id."""
        lst = self._list
        if lst.option_count == 0:
            return None
        idx = ((lst.highlighted or 0) + delta) % lst.option_count
        lst.highlighted = idx
        opt = lst.get_option_at_index(idx)
        return str(opt.id) if opt and opt.id else None

    def accept_highlighted(self) -> str | None:
        """Return the command id at the current highlight, or None."""
        lst = self._list
        opt = lst.get_option_at_index(lst.highlighted or 0)
        return str(opt.id) if opt and opt.id else None

    def refresh_suggest(
        self,
        value: str,
        commands: list[str],
        descriptions: dict[str, str],
    ) -> bool:
        """Repopulate and show/hide based on *value*.

        Returns True if the overlay became (or stayed) visible.
        """
        stripped = value.strip()
        if not stripped.startswith("/") or " " in stripped:
            self.hide()
            return False
        matches = [cmd for cmd in commands if cmd.startswith(stripped)]
        lst = self._list
        lst.clear_options()
        if not matches:
            self.hide()
            return False
        width = max(len(cmd) for cmd in matches[:SLASH_SUGGEST_MAX_OPTIONS])
        for cmd in matches[:SLASH_SUGGEST_MAX_OPTIONS]:
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append(f"{cmd:<{width}}", style="bold")
            desc = descriptions.get(cmd, "")
            if desc:
                label.append(f"   {desc}", style="dim")
            lst.add_option(Option(label, id=cmd))
        self.add_class("visible")
        lst.highlighted = 0
        return True

    # ── Event handling ────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        command = str(event.option.id or "")
        if command:
            self.post_message(self.Accepted(command))
