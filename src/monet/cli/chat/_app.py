"""Textual TUI for ``monet chat``.

Thin App shell: compose, reactive state, key bindings, event routing.
All business logic lives in :class:`~monet.cli.chat._session.SessionController`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Static

from monet.cli.chat._prompt import AutoGrowTextArea
from monet.cli.chat._session import SessionController
from monet.cli.chat._slash._overlay import SlashOverlay
from monet.cli.chat._status_bar import StatusBar
from monet.cli.chat._themes import MONET_EMBER, MONET_THEMES
from monet.cli.chat._transcript import Transcript

if TYPE_CHECKING:
    from monet.client import MonetClient

_log = logging.getLogger("monet.cli.chat")


class ChatApp(App[None]):  # type: ignore[misc]
    """Monet chat TUI — thin dispatcher over SessionController."""

    CSS_PATH = "_app.tcss"
    COMMANDS: ClassVar[set[Any]] = set()
    BINDINGS: ClassVar = [
        Binding("ctrl+q", "quit", show=False),
        Binding("ctrl+x", "cancel_run", show=False),
        Binding("tab", "tab_action", show=False, priority=True),
        Binding("shift+tab", "shift_tab_action", show=False, priority=True),
        Binding("escape", "escape_action", show=False),
    ]
    busy: reactive[bool] = reactive(False)
    thread_id: reactive[str] = reactive("")

    def __init__(
        self,
        *,
        client: MonetClient,
        thread_id: str,
        slash_commands: list[str] | None = None,
        transcript: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._initial_thread_id = thread_id
        self._initial_transcript = list(transcript or [])
        self._server_slash_commands = list(slash_commands or [])
        self._crash_error: BaseException | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="main-body"):
            yield SessionController(
                client=self._client,
                initial_thread_id=self._initial_thread_id,
                server_slash_commands=self._server_slash_commands,
                initial_transcript=self._initial_transcript,
                id="session",
            )
            yield Transcript(id="transcript")
            yield SlashOverlay(id="slash-suggest")
            with Horizontal(id="prompt-area"):
                yield Static("> ", id="prompt-glyph")
                yield AutoGrowTextArea(id="prompt")
            yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._session = self.query_one("#session", SessionController)
        self._session.setup(
            transcript=self.query_one("#transcript", Transcript),
            status_bar=self.query_one("#status-bar", StatusBar),
            prompt=self.query_one("#prompt", AutoGrowTextArea),
            slash_suggest=self.query_one("#slash-suggest", SlashOverlay),
        )
        for theme in MONET_THEMES:
            try:
                self.register_theme(theme)
            except Exception:
                _log.debug("theme registration failed: %s", theme.name, exc_info=True)
        self.theme = MONET_EMBER.name
        self.thread_id = self._initial_thread_id
        self.title = (
            f"monet chat · {self._initial_thread_id}"
            if self._initial_thread_id
            else "monet chat · (new)"
        )
        self._session.render_initial_history()
        if not self._initial_transcript and not self.thread_id:
            self.call_after_refresh(self._session.show_welcome)
        else:
            self._session._focus_prompt()

    def watch_busy(self, busy: bool) -> None:
        self.query_one("#status-bar", StatusBar).update_segments(
            active_run=self.thread_id[:8] if busy and self.thread_id else ""
        )

    def on_prompt_submitted(self, event: Any) -> None:
        self._session.handle_prompt_submitted(event)

    def on_hitl_submitted(self, msg: Any) -> None:
        self._session.handle_hitl_submitted(msg)

    def on_hitl_dismissed(self, msg: Any) -> None:
        self._session.handle_hitl_dismissed(msg)

    def action_cancel_run(self) -> None:
        self._session.cancel_run()

    def action_open_threads(self) -> None:
        self._session.open_threads(self.thread_id)

    def action_open_agents(self) -> None:
        self._session.open_agents()

    def action_open_artifacts(self) -> None:
        self._session.open_artifacts(self.thread_id)

    def action_open_runs(self) -> None:
        self._session.open_runs(self.thread_id)

    def action_open_shortcuts(self) -> None:
        from monet.cli.chat._screens import ShortcutsScreen

        self.push_screen(ShortcutsScreen())

    def action_tab_action(self) -> None:
        self._session.action_tab()

    def action_shift_tab_action(self) -> None:
        self._session.action_shift_tab()

    def action_escape_action(self) -> None:
        self._session.action_escape()

    def on_text_area_changed(self, event: Any) -> None:
        self._session.on_text_area_changed(event)

    def dismiss_welcome(self) -> None:
        self._session.dismiss_welcome()

    def prefill_input(self, text: str) -> None:
        self._session.prefill_input(text)

    def get_thread_id(self) -> str:
        return self.thread_id

    def set_thread_id(self, tid: str) -> None:
        self.thread_id = tid

    def set_title(self, title: str) -> None:
        self.title = title

    def update_status(self, **kwargs: Any) -> None:
        self.query_one("#status-bar", StatusBar).update_segments(**kwargs)

    def exit_app(self) -> None:
        self.exit()

    def push_screen_by_name(self, name: str) -> None:
        self._session.push_screen_by_name(name)

    def _mount_hitl_widgets(self, form: dict[str, Any]) -> bool:
        """Mount inline HITL widgets. Delegates to SessionController. Used by tests."""
        return self._session._mount_hitl_widgets(form)

    def _unmount_hitl_widgets(self) -> None:
        """Unmount HITL widgets. Delegates to SessionController. Used by tests."""
        self._session._unmount_hitl_widgets()

    async def _collect_resume(self, form: dict[str, Any]) -> dict[str, Any] | None:
        """Collect a HITL resume payload. Used by tests."""
        return await self._session.collect_resume(form)
