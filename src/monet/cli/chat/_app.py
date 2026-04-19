"""Textual TUI for ``monet chat``.

The :class:`ChatApp` is the composition root. Sub-systems live in
sibling modules:

- :mod:`~monet.cli.chat._pulse` — breathing border animation
- :mod:`~monet.cli.chat._welcome` — empty-state logo overlay
- :mod:`~monet.cli.chat._turn` — turn streaming + interrupt coordination
- :mod:`~monet.cli.chat._slash` — ghost-text suggester + command palette
- :mod:`~monet.cli.chat._pickers` — full-screen list picker
- :mod:`~monet.cli.chat._hitl` — interrupt form parsing
- :mod:`~monet.cli.chat._view` — transcript styling
- :mod:`~monet.cli.chat._constants` — magic values + welcome content
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Container, Horizontal
from textual.widgets import (
    Button,
    Header,
    Input,
    LoadingIndicator,
    OptionList,
    RichLog,
    Static,
)
from textual.widgets.option_list import Option

from monet.cli._namegen import random_chat_name
from monet.cli.chat._constants import (
    BUSY_PULSE_DURATION,
    BUSY_PULSE_PEAK,
    CUSTOM_BORDER_COLOR,
    DEFAULT_TOOLBAR_HINTS,
    EXIT_CONFIRM_TIMEOUT,
    IDLE_PULSE_DURATION,
    IDLE_PULSE_PEAK,
    INDICATOR_REFRESH_SECONDS,
    PULSE_ENABLED,
    TUI_COMMANDS,
)
from monet.cli.chat._hitl_form import (
    build_hitl_widget,
    build_submit_summary,
    envelope_supports_widgets,
)
from monet.cli.chat._menu import (
    MENU_ABOUT,
    MENU_EXIT,
    MENU_KEYBOARD,
    MENU_LIBRARY,
    MENU_OPTIONS,
    AboutScreen,
    CommandLibraryScreen,
    KeyboardShortcutsScreen,
    MainMenuScreen,
    OptionsScreen,
)
from monet.cli.chat._pickers import TablePickerScreen as _TablePickerScreen
from monet.cli.chat._pulse import BorderPulseController
from monet.cli.chat._sidebar import (
    BREAKPOINT_COLS,
    SidebarKind,
    SidebarPanel,
)
from monet.cli.chat._slash import RegistrySuggester
from monet.cli.chat._themes import MONET_DARK, MONET_THEMES
from monet.cli.chat._turn import (
    InterruptCoordinator,
    drain_stream,
    empty_stream,
    run_turn,
)
from monet.cli.chat._view import (
    DEFAULT_TAG_STYLES as _DEFAULT_TAG_STYLES,
)
from monet.cli.chat._view import (
    ROLE_TAGS as _ROLE_TAGS,
)
from monet.cli.chat._view import (
    styled_line as _styled_line,
)
from monet.cli.chat._welcome import WelcomeOverlay
from monet.config._user_chat import UserChatStyle as _UserChatStyle

_log = logging.getLogger("monet.cli.chat")


if TYPE_CHECKING:
    from textual.worker import Worker

    from monet.client import MonetClient


# --- Main app -------------------------------------------------------------


class ChatApp(App[None]):
    """Textual app wiring :class:`MonetClient` to a live chat REPL."""

    CSS = """
    Screen {
        background: black;
        overflow: hidden;
    }

    * {
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-background: black;
        scrollbar-background-hover: black;
        scrollbar-background-active: black;
        scrollbar-color: $accent 60%;
        scrollbar-color-hover: $accent;
        scrollbar-color-active: $accent;
        scrollbar-corner-color: black;
    }

    #transcript-area {
        height: 1fr;
        layers: base overlay;
    }

    #toolbar {
        dock: top;
        height: 1;
        padding: 0 1;
        background: black;
    }

    #thread-name {
        width: 32;
        height: 1;
        border: none;
        padding: 0 1;
        margin: 0;
        background: black;
        color: $text-muted;
    }

    #thread-name:focus {
        color: $text;
        background: $panel-lighten-1;
    }

    #toolbar-hints {
        color: $text-muted;
        width: 1fr;
        height: 1;
        content-align: center middle;
        background: black;
    }

    #toolbar Button {
        min-width: 8;
        height: 1;
        padding: 0 1;
        margin: 0;
        border: none;
        background: black;
    }

    #transcript {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
        background: black;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
    }

    #prompt {
        dock: bottom;
        height: 5;
        border: round $primary;
        padding: 0 1;
        margin: 0;
        background: black;
    }

    #slash-suggest {
        dock: bottom;
        height: auto;
        max-height: 8;
        margin: 0 0 5 0;
        border: round $accent;
        background: black;
        display: none;
    }

    #slash-suggest.visible {
        display: block;
    }

    #spinner {
        dock: bottom;
        height: 1;
        margin: 0 0 5 0;
        background: black;
        display: none;
    }

    #spinner.visible {
        display: block;
    }

    """

    # Strip Textual's default command-palette provider set. ``ctrl+p``
    # opens the monet-native :class:`MainMenuScreen` instead, so the
    # built-in palette (and all its system commands) must not register.
    COMMANDS: ClassVar[set[Any]] = set()
    BINDINGS: ClassVar = [
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+c", "confirm_quit", "Quit", show=False, priority=True),
        Binding("down", "focus_suggest", "Suggestions", show=False),
        Binding("escape", "hide_suggest", "Close", show=False),
        Binding("tab", "accept_suggestion", "Complete", show=False, priority=True),
        Binding("f1", "toggle_help_panel", "Keys", show=False),
        Binding("ctrl+p", "open_menu", "Menu", show=False, priority=True),
    ]

    def __init__(
        self,
        *,
        client: MonetClient,
        thread_id: str,
        slash_commands: list[str] | None = None,
        history: list[dict[str, Any]] | None = None,
        style: _UserChatStyle | None = None,
    ) -> None:
        super().__init__()
        self._client: MonetClient = client
        self._chat_thread_id: str = thread_id
        self._server_slash_commands: list[str] = list(slash_commands or [])
        self.slash_commands: list[str] = self._combined_slash_commands()
        # Per-command short descriptions shown in the completion dropdown.
        # Seeded from TUI defaults; ``refresh_slash_commands`` merges in
        # agent-command descriptions from the server capability manifest.
        self.slash_descriptions: dict[str, str] = dict(TUI_COMMANDS)
        self._suggester = RegistrySuggester(self.slash_commands)
        self._initial_history = list(history or [])
        self._busy = False
        self._transcript_lines: list[str] = []
        # HITL coordinator — holds the future resolved by the next
        # prompt submission when ``InterruptCoordinator.collect`` is
        # awaiting.
        self._interrupts = InterruptCoordinator()
        # Mounted HITL widget (InlinePicker or HITLForm) + parsed
        # envelope while a resume is awaiting. ``None`` in every other
        # state.
        self._hitl_widget: Any = None
        self._hitl_envelope: Any = None
        self._turn_worker: Worker[None] | None = None
        # Set by ``_handle_exception`` when a Textual-unhandled error
        # bubbles to the app. The CLI wrapper reads this after
        # ``run_async`` returns to show a friendly crash message instead
        # of a rich traceback.
        self._crash_error: BaseException | None = None
        # ctrl+c two-press confirm-exit state.
        self._exit_arm_handle: asyncio.TimerHandle | None = None
        # Toolbar indicator state. ``_indicator_text`` holds the most
        # recent "N agents · M artifacts" string; transient hints
        # (confirm-exit, error flash) set ``_indicator_override`` so the
        # periodic refresher knows not to clobber them. Restoring the
        # indicator just clears the override.
        self._indicator_text: str = DEFAULT_TOOLBAR_HINTS
        self._indicator_override: bool = False
        # Profile baseline — merged from built-in defaults + user profile.
        # ``/colors reset`` returns here; individual ``/colors`` changes
        # write to the live copies below without touching this baseline.
        _profile = style or _UserChatStyle()
        self._profile_tag_styles: dict[str, str] = _profile.tag_styles(
            _DEFAULT_TAG_STYLES
        )
        self._profile_border_color: str = _profile.border_color or ""
        # Live palette — starts from the profile baseline.
        self._tag_styles: dict[str, str] = dict(self._profile_tag_styles)
        # Border pulse controller. MONET_CHAT_BORDER_COLOR env var wins
        # over the profile for border (tmux pane differentiation).
        self._pulse = BorderPulseController(
            self,
            override_color=CUSTOM_BORDER_COLOR or self._profile_border_color,
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Disable slash-suggest bindings when a picker surface is active.

        Tab is registered as a priority App binding so the suggester can
        accept ghost-text from anywhere. Without this guard, pushed
        :class:`_PickerScreen` instances or a mounted :class:`SidebarPanel`
        would never see Tab / Escape / Down because the App consumes them
        first.
        """
        scoped = {"accept_suggestion", "focus_suggest", "hide_suggest"}
        if action not in scoped:
            return True
        if isinstance(self.screen, _TablePickerScreen):
            return False
        return not self.query("#sidebar")

    def _combined_slash_commands(self) -> list[str]:
        """TUI-level commands first, then server-declared slash commands."""
        out: list[str] = [cmd for cmd, _desc in TUI_COMMANDS]
        seen = set(out)
        for cmd in self._server_slash_commands:
            if cmd not in seen:
                out.append(cmd)
                seen.add(cmd)
        return out

    # ── UI ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="toolbar"):
            yield Input(
                placeholder=self._thread_name_placeholder(),
                id="thread-name",
            )
            yield Static(DEFAULT_TOOLBAR_HINTS, id="toolbar-hints")
            yield Button("⧉ copy", id="copy-transcript", variant="default")
        with Container(id="transcript-area"):
            yield RichLog(id="transcript", wrap=True, markup=False, highlight=False)
            yield WelcomeOverlay()
        yield OptionList(id="slash-suggest")
        yield LoadingIndicator(id="spinner")
        yield Input(
            placeholder="Type a message or /command…",
            id="prompt",
            suggester=self._suggester,
        )

    def _thread_name_placeholder(self) -> str:
        short = self._chat_thread_id[:8] if self._chat_thread_id else "(new)"
        return f"untitled · {short}"

    async def _ensure_thread_id(self) -> str:
        """Create the backing thread lazily on first real use.

        Default ``monet chat`` launch passes an empty id so idle
        sessions don't spam empty threads. The first user submission
        (or rename) allocates a thread and updates the title / toolbar.
        """
        if self._chat_thread_id:
            return self._chat_thread_id
        generated_name = random_chat_name()
        new_id = await self._client.chat.create_chat(name=generated_name)
        self._chat_thread_id = new_id
        self.title = f"monet chat · {generated_name}"
        with contextlib.suppress(Exception):
            thread_input = self.query_one("#thread-name", Input)
            thread_input.value = generated_name
            thread_input.placeholder = self._thread_name_placeholder()
        return new_id

    def _set_toolbar_hints(self, text: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#toolbar-hints", Static).update(text)

    def on_mount(self) -> None:
        # Register monet's own themes before first paint so CSS vars
        # like $primary / $accent resolve to the monet palette instead
        # of Textual's built-in defaults.
        for theme in MONET_THEMES:
            with contextlib.suppress(Exception):
                self.register_theme(theme)
        self.theme = MONET_DARK.name
        self.title = (
            f"monet chat · {self._chat_thread_id}"
            if self._chat_thread_id
            else "monet chat · (new)"
        )
        for msg in self._initial_history:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            self._append_line(f"[{role}] {content}")
        # Empty-state welcome: centered logo + key-command cheatsheet.
        # Not written to the thread backend; a returning user with prior
        # history sees their messages instead (no welcome).
        if not self._initial_history:
            self.call_after_refresh(self._show_welcome)
        self.query_one("#prompt", Input).focus()
        self.run_worker(self._load_thread_name(), exclusive=False)
        # Pull server-side slash-command descriptions so the completion
        # dropdown shows a hint next to each agent command on first
        # keystroke, not only after the first turn completes.
        self.run_worker(self.refresh_slash_commands(), exclusive=False)
        # Recover any interrupt that survived a server restart.
        if self._chat_thread_id:
            self.run_worker(self._recover_pending_interrupt(), exclusive=False)
        # Kick off the toolbar indicator (agent count + artifact count).
        self._refresh_indicator()
        self.set_interval(INDICATOR_REFRESH_SECONDS, self._refresh_indicator)
        # Paint both borders dim so the idle pulse reads as a clear swing
        # against a quiet baseline, then kick off the idle pulse itself.
        if PULSE_ENABLED:
            self._pulse.apply_idle_borders(("#transcript", "#prompt"))
        self._set_busy(False)

    def _refresh_indicator(self) -> None:
        """Recompute the toolbar indicator (agent count · artifact count).

        Agents come from the server (``client.list_capabilities``) —
        the chat TUI runs in its own process, so the in-process
        ``default_registry`` is always empty here. Artifacts come from
        ``get_artifacts().query_recent(thread_id=...)`` — best-effort,
        swallowed on any error so a missing backend never surfaces in
        the toolbar.
        """
        self.run_worker(self._refresh_indicator_async(), exclusive=False)

    async def _refresh_indicator_async(self) -> None:
        try:
            caps = await self._client.list_capabilities()
            agent_count = len({str(c.get("agent_id") or "") for c in caps} - {""})
        except Exception:
            agent_count = 0

        artifact_count = 0
        if self._chat_thread_id:
            try:
                from monet.core.artifacts import get_artifacts

                rows = await get_artifacts().query_recent(
                    thread_id=self._chat_thread_id, limit=10_000
                )
                artifact_count = len(rows)
            except Exception:
                artifact_count = 0

        text = f"{agent_count} agents · {artifact_count} artifacts"
        self._indicator_text = text
        # Don't clobber a transient hint (e.g. confirm-exit).
        if not self._indicator_override:
            self._set_toolbar_hints(text)

    def on_unmount(self) -> None:
        """Stop any running pulse before teardown so no tick fires against
        a widget that is being removed."""
        self._pulse.shutdown()

    def _handle_exception(self, error: Exception) -> None:
        """Route unhandled errors to the log file + exit gracefully.

        Textual's default prints a full rich traceback with locals to
        stdout on crash, which is unreadable for end users. Instead we
        log the traceback to the chat log file and call :meth:`exit`
        with a stored ``_crash_error`` so the CLI wrapper can show a
        one-line friendly message with the log path.
        """
        _log.error("unhandled exception in ChatApp", exc_info=error)
        self._crash_error = error
        self._return_code = 1
        if self._exception is None:
            self._exception = error
            self._exception_event.set()
        with contextlib.suppress(Exception):
            self.exit(return_code=1)

    async def _load_thread_name(self) -> None:
        """Populate the toolbar thread-name input from server metadata."""
        if not self._chat_thread_id:
            return
        try:
            name = await self._client.chat.get_chat_name(self._chat_thread_id)
        except Exception as exc:
            _log.debug("get_chat_name failed: %s", exc)
            return
        if not name:
            return
        with contextlib.suppress(Exception):
            self.query_one("#thread-name", Input).value = name

    def _set_spinner(self, visible: bool) -> None:
        """Show or hide the bottom loading indicator."""
        with contextlib.suppress(Exception):
            spinner = self.query_one("#spinner", LoadingIndicator)
            if visible:
                spinner.add_class("visible")
            else:
                spinner.remove_class("visible")

    # ── Busy / idle state + border pulse ─────────────────────────

    def _set_busy(self, busy: bool) -> None:
        """Update busy flag, spinner, and pulse target in one place.

        Busy state drives the transcript border pulse ("assistant is
        working"); idle drives the prompt border pulse ("ready for
        input"). HITL waits count as idle because the app is waiting
        for the user's resume reply.
        """
        self._busy = busy
        self._set_spinner(busy)
        if not PULSE_ENABLED:
            return
        if busy:
            self._pulse.stop("#prompt")
            self._pulse.start(
                "#transcript",
                peak_var=BUSY_PULSE_PEAK,
                duration=BUSY_PULSE_DURATION,
            )
        else:
            self._pulse.stop("#transcript")
            self._pulse.start(
                "#prompt",
                peak_var=IDLE_PULSE_PEAK,
                duration=IDLE_PULSE_DURATION,
            )

    # ── Welcome overlay ──────────────────────────────────────────

    def _show_welcome(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one(WelcomeOverlay).show()

    def _hide_welcome(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one(WelcomeOverlay).hide()

    # ── Transcript ───────────────────────────────────────────────

    def _append_line(self, line: str) -> None:
        """Write *line* to the transcript and buffer it for copy-to-clipboard.

        The plain-text version is kept for the clipboard copy button; the
        rendered version uses :func:`_styled_line` so the leading
        ``[role]`` tag renders in the configured colour.
        """
        self._hide_welcome()
        self._transcript_lines.append(line)
        with contextlib.suppress(Exception):
            self.query_one("#transcript", RichLog).write(
                _styled_line(line, self._tag_styles)
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy-transcript":
            self._copy_transcript()

    def _copy_transcript(self) -> None:
        text = "\n".join(self._transcript_lines)
        if not text:
            self.notify("transcript is empty", severity="warning")
            return
        try:
            self.copy_to_clipboard(text)
        except Exception as exc:
            _log.warning("copy_to_clipboard failed: %s", exc)
            self.notify(f"copy failed: {exc}", severity="error")
            return
        self.notify(f"copied {len(self._transcript_lines)} line(s)")

    # ── Public hooks used by providers and tests ──────────────────

    def prefill_input(self, text: str) -> None:
        """Replace the prompt input content and focus it."""
        prompt = self.query_one("#prompt", Input)
        prompt.value = text
        prompt.focus()
        prompt.cursor_position = len(text)

    async def refresh_slash_commands(self) -> None:
        """Reload the server slash-command list and refresh the suggester.

        Also pulls the capability manifest so agent-command entries
        (``/<agent>:<command>``) carry the agent's own description in
        the completion dropdown.
        """
        try:
            commands = await self._client.slash_commands()
        except Exception:
            _log.debug("refresh_slash_commands failed", exc_info=True)
            return
        self._server_slash_commands = commands
        self.slash_commands = self._combined_slash_commands()
        self._suggester.update(self.slash_commands)
        # Reset to TUI defaults before layering server-side descriptions
        # so a capability unregistered between refreshes drops out of
        # the dropdown hint.
        descriptions: dict[str, str] = dict(TUI_COMMANDS)
        try:
            caps = await self._client.list_capabilities()
        except Exception:
            _log.debug("list_capabilities failed during slash refresh", exc_info=True)
            caps = []
        for cap in caps:
            agent_id = str(cap.get("agent_id") or "")
            command = str(cap.get("command") or "")
            desc = str(cap.get("description") or "").strip()
            if agent_id and command and desc:
                descriptions[f"/{agent_id}:{command}"] = desc
        self.slash_descriptions = descriptions

    # ── Slash-command dropdown ────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter and show the slash-command dropdown as the user types."""
        if event.input.id != "prompt":
            return
        self._refresh_slash_suggest(event.value)

    def _refresh_slash_suggest(self, value: str) -> None:
        from rich.text import Text

        suggest = self.query_one("#slash-suggest", OptionList)
        stripped = value.strip()
        if not stripped.startswith("/") or " " in stripped:
            suggest.remove_class("visible")
            return
        matches = [cmd for cmd in self.slash_commands if cmd.startswith(stripped)]
        suggest.clear_options()
        if not matches:
            suggest.remove_class("visible")
            return
        # Column-align command names so descriptions line up evenly.
        width = max(len(cmd) for cmd in matches[:20])
        for cmd in matches[:20]:
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append(f"{cmd:<{width}}", style="bold")
            desc = self.slash_descriptions.get(cmd, "")
            if desc:
                label.append(f"   {desc}", style="dim")
            suggest.add_option(Option(label, id=cmd))
        suggest.add_class("visible")
        suggest.highlighted = 0

    def action_focus_suggest(self) -> None:
        suggest = self.query_one("#slash-suggest", OptionList)
        if "visible" in suggest.classes and suggest.option_count > 0:
            suggest.focus()

    def action_hide_suggest(self) -> None:
        suggest = self.query_one("#slash-suggest", OptionList)
        suggest.remove_class("visible")
        self.query_one("#prompt", Input).focus()

    async def action_accept_suggestion(self) -> None:
        """Fill the input with the current ghost-text suggestion, if any.

        Bound to ``tab`` so typing ``/pl`` then Tab expands to ``/plan``.
        Falls back to focus-move when the input has no suggestion.
        """
        prompt = self.query_one("#prompt", Input)
        if not prompt.has_focus:
            self.screen.focus_next()
            return
        suggestion = await self._suggester.get_suggestion(prompt.value)
        if not suggestion or suggestion == prompt.value:
            self.screen.focus_next()
            return
        prompt.value = suggestion
        prompt.cursor_position = len(suggestion)

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        if event.option_list.id != "slash-suggest":
            return
        chosen = str(event.option.id or "")
        if not chosen:
            return
        prompt = self.query_one("#prompt", Input)
        prompt.value = chosen + " "
        prompt.cursor_position = len(prompt.value)
        suggest = self.query_one("#slash-suggest", OptionList)
        suggest.remove_class("visible")
        prompt.focus()

    # ── Input submission ──────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Dispatch each submission and return immediately.

        The actual chat turn / interrupt-resume work runs in a worker
        so the Input widget's message pump stays free — otherwise a
        long-running ``await`` (especially the interrupt-resume future
        used by HITL) would block the pump and the user could not type
        the next message.
        """
        # Toolbar thread-name field: submit renames the thread.
        if event.input.id == "thread-name":
            new_name = event.value.strip()
            # Hand off; do not forward to the chat prompt path.
            self.run_worker(self._rename_thread(new_name), exclusive=False)
            self.query_one("#prompt", Input).focus()
            return
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        # TUI-local bailouts work regardless of interrupt state so a
        # user stuck at an approval prompt can still leave the app.
        if text in {"/quit", "/exit"}:
            self._append_line(f"[user] {text}")
            self.exit()
            return
        # If a turn is mid-flight waiting on a HITL resume, hand this
        # submission to the awaiting worker instead of starting a new
        # chat turn. The resumer prints [user] when it consumes it.
        if self._interrupts.is_pending():
            self._append_line(f"[user] {text}")
            self._interrupts.consume_if_pending(text)
            return
        if self._busy:
            head = text.split()[0] if text else ""
            if head in {"/new", "/clear"}:
                self._append_line(f"[user] {text}")
                if self._turn_worker is not None:
                    self._turn_worker.cancel()
                    self._turn_worker = None
                self._set_busy(False)
                log = self.query_one("#transcript", RichLog)
                self.run_worker(self._cmd_new_thread(log), exclusive=False)
            return
        # Detach. Worker reads `_busy` itself; we don't await it here.
        self._turn_worker = self.run_worker(
            self._handle_user_text(text), exclusive=False
        )

    async def _rename_thread(self, name: str) -> None:
        """Persist a thread rename and surface success / failure as a notify."""
        try:
            thread_id = await self._ensure_thread_id()
            await self._client.chat.rename_chat(thread_id, name)
        except Exception as exc:
            _log.warning("rename_chat failed: %s", exc)
            self.notify(f"rename failed: {exc}", severity="error")
            return
        label = name or "(untitled)"
        self.notify(f"thread renamed to {label}")

    # ── Quit (ctrl+c two-press confirm) ───────────────────────────

    def action_confirm_quit(self) -> None:
        """First press: show exit hint. Second press within window: exit."""
        if self._exit_arm_handle is not None:
            self._cancel_exit_arm()
            self.exit()
            return
        self._indicator_override = True
        self._set_toolbar_hints(
            f"press ctrl+c again within {int(EXIT_CONFIRM_TIMEOUT)}s to exit"
        )
        loop = asyncio.get_running_loop()
        self._exit_arm_handle = loop.call_later(
            EXIT_CONFIRM_TIMEOUT, self._reset_exit_arm
        )

    def _reset_exit_arm(self) -> None:
        self._exit_arm_handle = None
        self._indicator_override = False
        self._set_toolbar_hints(self._indicator_text)

    def _cancel_exit_arm(self) -> None:
        if self._exit_arm_handle is not None:
            self._exit_arm_handle.cancel()
            self._exit_arm_handle = None
        self._indicator_override = False
        self._set_toolbar_hints(self._indicator_text)

    async def _recover_pending_interrupt(self) -> None:
        """Resume any interrupt that survived a server restart.

        Called on mount, thread switch, and after a connection error so
        the TUI re-attaches to a HITL form that was waiting in the
        checkpointer when the server went down.
        """
        thread_id = self._chat_thread_id
        if not thread_id:
            return
        try:
            pending = await self._client.chat.get_chat_interrupt(thread_id)
        except Exception:
            return
        if not pending:
            return
        _log.info("recovering pending interrupt on thread=%s", thread_id)
        self._append_line("[info] pending approval found — resuming")

        self._set_busy(True)
        try:
            await run_turn(
                client=self._client,
                thread_id=thread_id,
                first_stream=empty_stream(),
                coordinator=self._interrupts,
                writer=self._append_line,
                busy_setter=self._set_busy,
                focus_prompt=self._focus_prompt,
                get_interrupt=self._client.chat.get_chat_interrupt,
                resume=self._client.chat.resume_chat,
                mount_widgets=self._mount_hitl_widgets,
                unmount_widgets=self._unmount_hitl_widgets,
            )
        except Exception as exc:
            self._append_line(f"[error] {exc}")
            _log.exception("interrupt recovery failed")
        self._set_busy(False)

    async def _handle_user_text(self, text: str) -> None:
        """Run one user submission to completion in a worker context."""
        log = self.query_one("#transcript", RichLog)
        self._append_line(f"[user] {text}")
        _log.info("user submit thread=%s text=%r", self._chat_thread_id, text)
        if text in {"/quit", "/exit"}:
            self.exit()
            return
        if await self._maybe_run_tui_command(text, log):
            return
        self._set_busy(True)
        self.sub_title = "thinking…"
        self._append_line("[info] thinking…")
        try:
            thread_id = await self._ensure_thread_id()
            stream = self._client.chat.send_message(thread_id, text)
            await run_turn(
                client=self._client,
                thread_id=thread_id,
                first_stream=stream,
                coordinator=self._interrupts,
                writer=self._append_line,
                busy_setter=self._set_busy,
                focus_prompt=self._focus_prompt,
                get_interrupt=self._client.chat.get_chat_interrupt,
                resume=self._client.chat.resume_chat,
                mount_widgets=self._mount_hitl_widgets,
                unmount_widgets=self._unmount_hitl_widgets,
            )
        except Exception as exc:
            self._append_line(f"[error] {exc}")
            _log.exception("chat turn failed")
            # Server may have restarted mid-execution; recover any
            # interrupt that survived in the checkpointer.
            await self._recover_pending_interrupt()
        finally:
            self.sub_title = ""
            self._set_busy(False)
            self._turn_worker = None
            # Turn finished — any new artifacts now want counting.
            self._refresh_indicator()

    def _focus_prompt(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#prompt", Input).focus()

    def action_toggle_help_panel(self) -> None:
        """Toggle Textual's key/help side panel (bound to ``f1``).

        Textual exposes ``show_help_panel`` / ``hide_help_panel`` actions
        via the command palette but binds neither by default, which
        leaves users stranded once they've opened the panel from the
        palette.
        """
        from textual.widgets import HelpPanel

        if self.screen.query(HelpPanel):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()

    # ── Main menu (ctrl+p) ────────────────────────────────────────

    def action_open_menu(self) -> None:
        """``ctrl+p`` — open the monet-native menu."""
        self.push_screen(MainMenuScreen(), self._on_menu_pick)

    def _on_menu_pick(self, section: str | None) -> None:
        """Route the top-level menu choice to the matching sub-screen."""
        if not section:
            return
        if section == MENU_EXIT:
            self.exit()
            return
        if section == MENU_KEYBOARD:
            self.push_screen(KeyboardShortcutsScreen(), self._reopen_menu)
            return
        if section == MENU_OPTIONS:
            self.push_screen(
                OptionsScreen(
                    current_theme=str(self.theme or MONET_DARK.name),
                    themes=tuple(t.name for t in MONET_THEMES),
                    pulse_enabled=PULSE_ENABLED,
                ),
                self._on_options_pick,
            )
            return
        if section == MENU_LIBRARY:
            entries = [
                (cmd, self.slash_descriptions.get(cmd, ""))
                for cmd in self.slash_commands
            ]
            self.push_screen(CommandLibraryScreen(entries), self._on_library_pick)
            return
        if section == MENU_ABOUT:
            self.push_screen(AboutScreen(), self._reopen_menu)
            return

    def _reopen_menu(self, _result: str | None) -> None:
        """Pop a sub-screen then re-present the top-level menu."""
        self.push_screen(MainMenuScreen(), self._on_menu_pick)

    def _on_options_pick(self, result: str | None) -> None:
        if not result:
            return
        if result.startswith("theme:"):
            theme_name = result.split(":", 1)[1]
            with contextlib.suppress(Exception):
                self.theme = theme_name
            self._append_line(f"[info] theme set to {theme_name}")
        # (pulse toggle intentionally not wired yet — requires a runtime
        # override to ``PULSE_ENABLED`` constant; revisit when the user
        # asks for it.)

    def _on_library_pick(self, result: str | None) -> None:
        if not result:
            return
        self.prefill_input(result + " ")

    # ── Picker (sidebar vs fullscreen) ────────────────────────────

    def _sidebar_mounted(self) -> bool:
        return bool(self.query("#sidebar"))

    async def _open_picker(self, kind: SidebarKind) -> None:
        """Open a picker for *kind*.

        Width-based dispatch: wide terminals mount the right-docked
        :class:`SidebarPanel`; narrow terminals push a full-screen
        :class:`_PickerScreen`. Hard floor (:data:`FLOOR_COLS`) always
        pushes full-screen.
        """
        if self._sidebar_mounted():
            # Already open — re-invocation flips to the newly-requested kind.
            self._close_sidebar()
        if self.size.width < BREAKPOINT_COLS:
            await self._push_fullscreen_picker(kind)
            return
        self._mount_sidebar(kind)

    def _mount_sidebar(self, kind: SidebarKind) -> None:
        panel = SidebarPanel(
            kind=kind,
            client=self._client,
            thread_id_getter=lambda: self._chat_thread_id,
            on_select=self._on_sidebar_select,
            on_close=self._close_sidebar,
            on_fullscreen=self._go_fullscreen,
            on_delete=self._on_sidebar_delete,
        )
        try:
            area = self.query_one("#transcript-area")
            area.mount(panel)
        except Exception:
            _log.exception("failed to mount sidebar")

    async def _push_fullscreen_picker(self, kind: SidebarKind) -> None:
        title, columns, rows = await self._picker_options(kind)
        if not rows:
            # _picker_options already wrote the empty-state toast/line.
            return

        on_delete = None
        if kind == "threads":
            on_delete = self._fullscreen_delete_thread

        def _on_pick(result: str | None) -> None:
            if result is None:
                self._focus_prompt()
                return
            self._on_sidebar_select(kind, result)

        self.push_screen(
            _TablePickerScreen(title, columns, rows, on_delete=on_delete), _on_pick
        )

    async def _picker_options(
        self, kind: SidebarKind
    ) -> tuple[str, list[str], list[tuple[str, ...]]]:
        """Fetch options for the fullscreen picker. Mirrors sidebar fill.

        Returns ``(title, columns, rows)`` where each row is
        ``(key, col1, col2, …)``.
        """
        if kind == "agents":
            try:
                caps = await self._client.list_capabilities()
            except Exception as exc:
                self._append_line(f"[error] /agents failed: {exc}")
                return "Select an agent command", [], []
            if not caps:
                self._append_line("[info] no agents registered on this server")
                return "Select an agent command", [], []
            columns = ["Command", "Pool", "Description"]
            rows: list[tuple[str, ...]] = []
            for cap in sorted(
                caps,
                key=lambda c: (c.get("agent_id") or "", c.get("command") or ""),
            ):
                agent_id = str(cap.get("agent_id") or "")
                command = str(cap.get("command") or "")
                if not agent_id or not command:
                    continue
                pool = str(cap.get("pool") or "local")
                desc = str(cap.get("description") or "").strip()
                value = f"/{agent_id}:{command}"
                rows.append((value, value, pool, desc or "—"))
            return "Select an agent command", columns, rows
        if kind == "threads":
            try:
                chats = await self._client.chat.list_chats()
            except Exception as exc:
                self._append_line(f"[error] /threads failed: {exc}")
                return "Select a chat thread", [], []
            if not chats:
                self._append_line("[info] no chat threads yet")
                return "Select a chat thread", [], []
            columns = ["Name", "Messages", "ID"]
            rows = []
            for c in chats:
                marker = "▶ " if c.thread_id == self._chat_thread_id else ""
                name = marker + (c.name or "(unnamed)")
                rows.append((c.thread_id, name, str(c.message_count), c.thread_id[:8]))
            return "Select a chat thread", columns, rows
        # artifacts
        thread_id = self._chat_thread_id or ""
        artifact_rows: list[Any] = []
        if thread_id:
            try:
                from monet.core.artifacts import get_artifacts

                artifact_rows = list(
                    await get_artifacts().query_recent(thread_id=thread_id, limit=50)
                )
            except Exception as exc:
                self._append_line(f"[error] /artifacts failed: {exc}")
                return "Select an artifact", [], []
        if not artifact_rows:
            self._append_line("[info] no artifacts in this thread")
            return "Select an artifact", [], []
        columns = ["Key", "Kind", "ID"]
        rows = []
        for row in artifact_rows:
            art_id = str(getattr(row, "artifact_id", "") or getattr(row, "id", ""))
            kind_str = str(getattr(row, "kind", "") or "—")
            key = str(getattr(row, "key", "") or "—")
            rows.append((art_id, key, kind_str, art_id[:8]))
        return "Select an artifact", columns, rows

    def _close_sidebar(self) -> None:
        with contextlib.suppress(Exception):
            panel = self.query_one(SidebarPanel)
            panel.remove()
        self._focus_prompt()

    def _go_fullscreen(self, kind: SidebarKind) -> None:
        """Sidebar requested fullscreen — unmount and push the picker screen."""
        with contextlib.suppress(Exception):
            self.query_one(SidebarPanel).remove()
        self.run_worker(
            self._push_fullscreen_picker(kind),
            exclusive=True,
            group="picker-flip",
        )

    def _on_sidebar_select(self, kind: SidebarKind, value: str) -> None:
        """Route a picker selection to the right handler by kind."""
        if kind == "agents":
            self.prefill_input(value + " ")
            self._close_sidebar()
            return
        if kind == "threads":
            self._close_sidebar()
            self.run_worker(self._switch_thread(value), exclusive=True)
            return
        if kind == "artifacts":
            self._copy_artifact_url(value)
            return

    def _on_sidebar_delete(self, kind: SidebarKind, value: str) -> None:
        """Called by SidebarPanel after a successful thread delete."""
        if kind != "threads":
            return
        if value == self._chat_thread_id:
            log = self.query_one("#transcript", RichLog)
            self.run_worker(self._cmd_new_thread(log), exclusive=False)
        self._refresh_indicator()

    async def _fullscreen_delete_thread(self, thread_id: str) -> bool:
        """Delete callback passed to TablePickerScreen for the threads kind."""
        try:
            await self._client.chat.delete_chat(thread_id)
        except Exception as exc:
            self._append_line(f"[error] delete failed: {exc}")
            return False
        if thread_id == self._chat_thread_id:
            log = self.query_one("#transcript", RichLog)
            await self._cmd_new_thread(log)
        self._refresh_indicator()
        return True

    def on_resize(self, event: Any) -> None:
        """Swap sidebar → fullscreen picker if the terminal shrank below breakpoint."""
        del event
        if not self._sidebar_mounted():
            return
        if self.size.width >= BREAKPOINT_COLS:
            return
        with contextlib.suppress(Exception):
            panel = self.query_one(SidebarPanel)
            kind: SidebarKind = panel.kind
            panel.remove()
            self.run_worker(
                self._push_fullscreen_picker(kind),
                exclusive=True,
                group="picker-flip",
            )

    def _copy_artifact_url(self, artifact_id: str) -> None:
        """Copy the server's ``/artifacts/<id>/view`` URL to clipboard.

        Gives the operator a quick way to ctrl+click or paste the link
        into a browser without leaving chat.
        """
        # ``MonetClient`` stores the server URL as ``_url``; fall back
        # to an empty string if the attribute is missing so the copy
        # still works (URL will just be relative).
        base = getattr(self._client, "_url", "") or ""
        url = f"{base.rstrip('/')}/api/v1/artifacts/{artifact_id}/view"
        try:
            self.copy_to_clipboard(url)
            self.notify(f"copied artifact url · {artifact_id[:8]}")
        except Exception as exc:
            _log.warning("copy_to_clipboard failed: %s", exc)
            self.notify(f"copy failed: {exc}", severity="error")

    async def _collect_resume(
        self,
        form: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Render *form* in the transcript and parse the next user reply.

        Thin wrapper over :meth:`InterruptCoordinator.collect` kept as a
        method so tests can drive it directly via ``app._collect_resume``.
        """
        return await self._interrupts.collect(
            form,
            writer=self._append_line,
            busy_setter=self._set_busy,
            focus_prompt=self._focus_prompt,
            mount_widgets=self._mount_hitl_widgets,
            unmount_widgets=self._unmount_hitl_widgets,
        )

    # ── HITL inline widgets ──────────────────────────────────────────

    def _mount_hitl_widgets(self, form: dict[str, Any]) -> bool:
        """Mount a widget tree for *form* via the TUI's render protocols.

        Returns True when widgets were mounted (skip transcript-prompt
        rendering, use widgets for resume), False when the app should
        fall back to the existing text-parse path.
        """
        from monet.types import InterruptEnvelope

        envelope = InterruptEnvelope.from_interrupt_values(form)
        if envelope is None or not envelope_supports_widgets(envelope):
            return False
        try:
            widget = build_hitl_widget(envelope, self._on_hitl_payload)
            area = self.query_one("#transcript-area")
            area.mount(widget)
        except Exception:
            _log.exception("mount hitl widgets failed")
            return False
        self._hitl_widget = widget
        self._hitl_envelope = envelope
        if envelope.prompt:
            self._append_line(f"[info] {envelope.prompt}")
        # Neutral hint — vocabulary-free so it works for any envelope
        # whose render protocol matched. Text-path fallback stays
        # advertised for users who prefer typing.
        self._append_line("[info] select an option, or type a reply")
        return True

    def _unmount_hitl_widgets(self) -> None:
        widget = self._hitl_widget
        self._hitl_widget = None
        self._hitl_envelope = None
        if widget is None:
            return
        with contextlib.suppress(Exception):
            widget.remove()

    def _on_hitl_payload(self, payload: dict[str, Any] | None) -> None:
        """Callback handed to every mounted HITL widget.

        ``None`` means the widget tried to submit but found a required
        field empty — surface a warning and leave the widget mounted
        so the user can fix it. A valid dict is written to transcript
        and handed to the :class:`InterruptCoordinator`.
        """
        envelope = self._hitl_envelope
        if envelope is None:
            return
        if payload is None:
            self.notify("please fill in every required field", severity="warning")
            return
        summary = build_submit_summary(envelope, payload)
        self._append_line(f"[user] {summary}")
        self._interrupts.consume_payload(payload)

    async def _drain_stream(
        self,
        log: RichLog,
        stream: Any,
        *,
        source: str,
    ) -> bool:
        """Thin wrapper over :func:`drain_stream` preserving the test surface."""
        del log  # transcript writer is bound below
        return await drain_stream(
            stream,
            self._append_line,
            source=source,
            client=self._client,
            thread_id=self._chat_thread_id,
        )

    # ── TUI-level slash commands ──────────────────────────────────

    async def _maybe_run_tui_command(self, text: str, log: RichLog) -> bool:
        """Dispatch TUI-local slash commands. Returns True when handled."""
        head, _, rest = text.partition(" ")
        arg = rest.strip()
        if head in {"/new", "/clear"}:
            await self._cmd_new_thread(log)
            return True
        if head == "/threads":
            await self._open_picker("threads")
            return True
        if head == "/switch":
            await self._cmd_switch_thread(log, arg)
            return True
        if head == "/agents":
            await self._open_picker("agents")
            return True
        if head == "/artifacts":
            await self._open_picker("artifacts")
            return True
        if head == "/runs":
            await self._cmd_list_runs(log)
            return True
        if head == "/colors":
            self._cmd_colors(arg)
            return True
        if head == "/help":
            self._cmd_help(log)
            return True
        return False

    async def _cmd_new_thread(self, log: RichLog) -> None:
        generated_name = random_chat_name()
        try:
            new_id = await self._client.chat.create_chat(name=generated_name)
        except Exception as exc:
            self._append_line(f"[error] /new failed: {exc}")
            return
        self._chat_thread_id = new_id
        self.title = f"monet chat · {generated_name}"
        with contextlib.suppress(Exception):
            thread_input = self.query_one("#thread-name", Input)
            thread_input.value = generated_name
            thread_input.placeholder = self._thread_name_placeholder()
        self._reset_transcript(f"[info] new thread · {generated_name} · {new_id[:8]}")
        self.call_after_refresh(self._show_welcome)
        self._refresh_indicator()

    async def _switch_thread(self, target: str) -> None:
        log = self.query_one("#transcript", RichLog)
        await self._cmd_switch_thread(log, target)

    async def _cmd_switch_thread(self, log: RichLog, target: str) -> None:
        if not target:
            self._append_line("[info] usage: /switch <thread_id>")
            return
        try:
            history = await self._client.chat.get_chat_history(target)
        except Exception as exc:
            self._append_line(f"[error] /switch failed: {exc}")
            return
        self._chat_thread_id = target
        self.title = f"monet chat · {target}"
        with contextlib.suppress(Exception):
            thread_input = self.query_one("#thread-name", Input)
            thread_input.value = ""
            thread_input.placeholder = self._thread_name_placeholder()
        self.run_worker(self._load_thread_name(), exclusive=False)
        self._reset_transcript(f"[info] switched to {target}")
        for msg in history:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            self._append_line(f"[{role}] {content}")
        if not history:
            self.call_after_refresh(self._show_welcome)
        self._refresh_indicator()
        self.run_worker(self._recover_pending_interrupt(), exclusive=False)

    def _reset_transcript(self, first_line: str | None = None) -> None:
        """Clear the transcript RichLog and its copy buffer in lockstep."""
        self._transcript_lines = []
        with contextlib.suppress(Exception):
            self.query_one("#transcript", RichLog).clear()
        if first_line:
            self._append_line(first_line)

    async def _cmd_list_runs(self, log: RichLog) -> None:
        """Print recent pipeline runs to the transcript.

        Chat-only runs (those whose single completed stage is the
        configured chat graph id) are filtered out so the log focuses
        on planning / execution activity. Reads the id from the client
        so user-configured chat graphs are filtered correctly.
        """
        try:
            runs = await self._client.list_runs(limit=20)
        except Exception as exc:
            self._append_line(f"[error] /runs failed: {exc}")
            return
        chat_graph = self._client.chat._chat_graph_id
        filtered = [r for r in runs if not (set(r.completed_stages) <= {chat_graph})]
        if not filtered:
            self._append_line("[info] no pipeline runs yet")
            return
        self._append_line(f"[info] {len(filtered)} recent pipeline run(s):")
        for r in filtered:
            stages = ", ".join(r.completed_stages) or "(none)"
            created = (r.created_at or "")[:19]
            rid = (r.run_id or "")[:8]
            self._append_line(f"  {created}  {r.status:<12}  {rid}  stages=[{stages}]")

    def _cmd_colors(self, arg: str) -> None:
        """Show or mutate the transcript / border palette for this session.

        Usage (all args optional)::

            /colors                        show current palette
            /colors reset                  restore defaults
            /colors <target> <colour>      change one target

        Targets: ``border``, ``user``, ``assistant``, ``info``,
        ``progress``, ``error``. Colours accept any Textual-parseable
        string: hex (``#ff3366``), named (``red``, ``cyan``), or
        ``rgb(...)``. Changes are session-only; persist them in
        ``~/.monet/chat.toml [style]`` for a permanent profile.
        """
        parts = arg.split() if arg else []
        if not parts:
            self._print_color_palette()
            return
        if len(parts) == 1 and parts[0] == "reset":
            self._tag_styles = dict(self._profile_tag_styles)
            self._pulse.override_color = (
                CUSTOM_BORDER_COLOR or self._profile_border_color
            )
            self._refresh_active_pulse()
            self._append_line("[info] colors reset to profile")
            return
        if len(parts) != 2:
            self._append_line(
                "[error] usage: /colors | /colors reset | /colors <target> <colour>"
            )
            return
        target, value = parts[0].lower(), parts[1]
        try:
            parsed = Color.parse(value)
        except Exception:
            self._append_line(
                f"[error] '{value}' is not a valid colour (try hex like #3b82f6 "
                f"or a name like cyan)"
            )
            return
        if target == "border":
            self._pulse.override_color = value
            self._refresh_active_pulse()
            self._append_line(f"[info] border colour set to {value}")
            return
        tag = _ROLE_TAGS.get(target)
        if tag is None:
            known = ", ".join(("border", *_ROLE_TAGS.keys()))
            self._append_line(f"[error] unknown target '{target}' (try: {known})")
            return
        # Preserve the existing modifier (``bold``) so /colors keeps the
        # role tag visually distinct from the following content.
        existing = self._tag_styles.get(tag, "")
        modifier = "bold " if "bold" in existing.split() else ""
        self._tag_styles[tag] = f"{modifier}{parsed.hex}".strip()
        self._append_line(f"[info] [{target}] colour set to {value}")

    def _print_color_palette(self) -> None:
        """Render the current palette as transcript lines."""
        self._append_line("[info] current colors (session):")
        border = self._pulse.override_color or "(theme $accent)"
        self._append_line(f"  border      {border}")
        for target, tag in _ROLE_TAGS.items():
            style = self._tag_styles.get(tag, "")
            self._append_line(f"  {target:<11} {style}")
        self._append_line("[info] set via: /colors <target> <colour> | /colors reset")

    def _refresh_active_pulse(self) -> None:
        """Restart whichever pulse is active so a new border colour applies."""
        if not PULSE_ENABLED:
            return
        for selector in self._pulse.active_selectors():
            self._pulse.stop(selector)
        self._set_busy(self._busy)

    def _cmd_help(self, log: RichLog) -> None:
        self._append_line("[info] TUI commands:")
        self._append_line("  /new, /clear        start a fresh thread")
        self._append_line("  /threads            open the thread picker")
        self._append_line("  /switch <thread>    resume an existing thread by id")
        self._append_line("  /agents             open the agent-command picker")
        self._append_line("  /runs               list recent pipeline runs")
        self._append_line("  /colors             show or change border + tag palette")
        self._append_line("  /quit, /exit        leave the REPL")
        self._append_line("[info] server-side slash commands:")
        for cmd in self._server_slash_commands[:20]:
            self._append_line(f"  {cmd} <task>")
