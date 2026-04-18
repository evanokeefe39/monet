"""Slash-command completion for the monet chat TUI."""

from __future__ import annotations

from typing import Any

from textual.command import Hit, Hits, Provider
from textual.suggester import Suggester


class RegistrySuggester(Suggester):
    """Ghost-text suggester backed by a live slash-command list.

    The list is expected to include reserved prefixes (``/plan``) and
    ``/<agent_id>:<command>`` entries from the server manifest.
    """

    def __init__(self, commands: list[str]) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self._commands = list(commands)

    def update(self, commands: list[str]) -> None:
        """Replace the command list without rebuilding the suggester."""
        self._commands = list(commands)

    async def get_suggestion(self, value: str) -> str | None:
        if not value.startswith("/"):
            return None
        for cmd in self._commands:
            if cmd.startswith(value) and cmd != value:
                return cmd
        return None


class SlashCommandProvider(Provider):
    """Command-palette provider exposing the live slash-command list.

    Selecting an entry inserts the command prefix into the chat input
    so the user can finish typing the task.
    """

    async def search(self, query: str) -> Hits:
        app: Any = self.app
        commands: list[str] = getattr(app, "slash_commands", []) or []
        matcher = self.matcher(query)
        for cmd in commands:
            score = matcher.match(cmd)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(cmd),
                    (lambda c=cmd: app.prefill_input(c + " ")),
                    help="slash command",
                )
