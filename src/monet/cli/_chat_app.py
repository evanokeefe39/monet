"""Textual TUI for ``monet chat``.

Replaces the :func:`click.prompt`-based REPL with a richer terminal UI:

- :class:`RichLog` transcript with markdown support for assistant replies.
- :class:`Input` prompt wired to :class:`RegistrySuggester` for ghost-text
  slash-command completion.
- A :class:`SlashCommandProvider` registered with the built-in command
  palette (``ctrl+p``) so users can browse the live registry.
- HITL interrupts render as transcript text and the next user message is
  parsed as the resume payload (no modal — modals proved unresponsive
  in real terminals; the prompt Input is the one widget we trust).

The app is driven by a :class:`~monet.client.MonetClient`; the Click
entry point in :mod:`monet.cli._chat` resolves the thread, builds the
client, and calls :meth:`ChatApp.run_async`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import TYPE_CHECKING, Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal
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

from monet.cli._chat_hitl import (
    format_form_prompt as _format_form_prompt,
)
from monet.cli._chat_hitl import (
    parse_text_reply as _parse_text_reply,
)
from monet.cli._chat_pickers import PickerScreen as _PickerScreen
from monet.cli._chat_slash import RegistrySuggester, SlashCommandProvider
from monet.cli._chat_view import (
    DEFAULT_TAG_STYLES as _DEFAULT_TAG_STYLES,
)
from monet.cli._chat_view import (
    ROLE_TAGS as _ROLE_TAGS,
)
from monet.cli._chat_view import (
    format_progress_line as _format_progress_line,
)
from monet.cli._chat_view import (
    styled_line as _styled_line,
)
from monet.cli._namegen import random_chat_name
from monet.client._events import AgentProgress
from monet.config import MONET_CHAT_BORDER_COLOR, MONET_CHAT_PULSE
from monet.config._user_chat import UserChatStyle as _UserChatStyle

_log = logging.getLogger("monet.cli.chat")


if TYPE_CHECKING:
    from monet.client import MonetClient


#: Slash commands handled by the TUI itself (not forwarded to the server).
TUI_COMMANDS: tuple[str, ...] = (
    "/new",
    "/clear",
    "/threads",
    "/switch",
    "/agents",
    "/runs",
    "/colors",
    "/help",
    "/quit",
    "/exit",
)

#: Default hints shown in the toolbar center.
_DEFAULT_TOOLBAR_HINTS = "/new  ·  /threads  ·  /agents  ·  /quit"

#: Seconds the toolbar holds the confirm-exit hint before disarming ctrl+c.
_EXIT_CONFIRM_TIMEOUT = 5.0

#: Border pulse is enabled unless the operator opts out via env var.
_PULSE_ENABLED = os.environ.get(MONET_CHAT_PULSE, "1").lower() not in {
    "0",
    "off",
    "false",
    "no",
}

#: Operator-supplied border colour for tmux / multi-pane differentiation.
#: Accepts any Textual-parseable color string (hex, named, rgb(...)). When
#: set, it becomes the pulse PEAK colour for both busy and idle pulses, so
#: every pane carries a distinct "signature" hue. Unset → theme ``$accent``.
_CUSTOM_BORDER_COLOR = os.environ.get(MONET_CHAT_BORDER_COLOR, "").strip()

#: Seconds per half-cycle for the border breath. Same rhythm across both
#: widgets so the transition from idle-prompt to busy-transcript feels
#: like a single pulse passing between the two rather than two different
#: speeds.
_BUSY_PULSE_DURATION = 1.0
_IDLE_PULSE_DURATION = 1.0

#: Theme-variable names used as the pulse peak when no
#: ``MONET_CHAT_BORDER_COLOR`` override is set.
_BUSY_PULSE_PEAK = "accent"
_IDLE_PULSE_PEAK = "accent"

#: Theme-variable name for the resting / inactive border colour. Deliberately
#: dim so the pulse reads as a clear contrast swing against this base.
_IDLE_BORDER_VAR = "panel-lighten-2"


# --- Main app -------------------------------------------------------------


class ChatApp(App[None]):
    """Textual app wiring :class:`MonetClient` to a live chat REPL."""

    CSS = """
    Screen {
        background: black;
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

    COMMANDS: ClassVar = App.COMMANDS | {SlashCommandProvider}
    BINDINGS: ClassVar = [
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+c", "confirm_quit", "Quit", show=False, priority=True),
        Binding("down", "focus_suggest", "Suggestions", show=False),
        Binding("escape", "hide_suggest", "Close", show=False),
        Binding("tab", "accept_suggestion", "Complete", show=False, priority=True),
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
        self._suggester = RegistrySuggester(self.slash_commands)
        self._initial_history = list(history or [])
        self._busy = False
        self._transcript_lines: list[str] = []
        # Set when the next user submission should be treated as a
        # HITL resume payload instead of a chat message. Resolved by
        # ``on_input_submitted``; awaited by ``_collect_resume``.
        self._pending_resume: asyncio.Future[str] | None = None
        # ctrl+c two-press confirm-exit state.
        self._exit_arm_handle: asyncio.TimerHandle | None = None
        # Per-widget pulse timer. ``_start_pulse`` schedules a
        # set_interval that updates the widget's border colors on each
        # tick; ``_stop_pulse`` cancels the interval handle and restores
        # the base color. Border color is not in Textual's animatable
        # property list (see RenderStyles.ANIMATABLE) so we interpolate
        # manually via Color.blend.
        self._pulse_timers: dict[str, Any] = {}
        # Profile baseline — merged from built-in defaults + user profile.
        # ``/colors reset`` returns here; individual ``/colors`` changes
        # write to the live copies below without touching this baseline.
        _profile = style or _UserChatStyle()
        self._profile_tag_styles: dict[str, str] = _profile.tag_styles(
            _DEFAULT_TAG_STYLES
        )
        self._profile_border_color: str = _profile.border_color or ""
        # Live palette — starts from the profile baseline. MONET_CHAT_BORDER_COLOR
        # env var wins over the profile for border (tmux pane differentiation).
        self._tag_styles: dict[str, str] = dict(self._profile_tag_styles)
        self._border_color_override: str = (
            _CUSTOM_BORDER_COLOR or self._profile_border_color
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Disable slash-suggest bindings when a list picker is active.

        Tab is registered as a priority App binding so the suggester can
        accept ghost-text from anywhere. Without this guard, pushed
        ``_PickerScreen`` instances would never see Tab / Escape / Down
        because the App consumes them first.
        """
        scoped = {"accept_suggestion", "focus_suggest", "hide_suggest"}
        if action not in scoped:
            return True
        return not isinstance(self.screen, _PickerScreen)

    def _combined_slash_commands(self) -> list[str]:
        """TUI-level commands first, then server-declared slash commands."""
        out: list[str] = list(TUI_COMMANDS)
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
            yield Static(_DEFAULT_TOOLBAR_HINTS, id="toolbar-hints")
            yield Button("⧉ copy", id="copy-transcript", variant="default")
        yield RichLog(id="transcript", wrap=True, markup=False, highlight=False)
        yield OptionList(id="slash-suggest")
        yield LoadingIndicator(id="spinner")
        yield Input(
            placeholder="Type a message or /command…",
            id="prompt",
            suggester=self._suggester,
        )

    def _thread_name_placeholder(self) -> str:
        short = self._chat_thread_id[:8] if self._chat_thread_id else "(none)"
        return f"untitled · {short}"

    def _set_toolbar_hints(self, text: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#toolbar-hints", Static).update(text)

    def on_mount(self) -> None:
        self.title = f"monet chat · {self._chat_thread_id}"
        for msg in self._initial_history:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            self._append_line(f"[{role}] {content}")
        self.query_one("#prompt", Input).focus()
        self.run_worker(self._load_thread_name(), exclusive=False)
        # Paint both borders dim so the idle pulse reads as a clear swing
        # against a quiet baseline, then kick off the idle pulse itself.
        if _PULSE_ENABLED:
            self._apply_idle_borders()
        self._set_busy(False)

    def on_unmount(self) -> None:
        """Stop any running pulse before teardown so no tick fires against
        a widget that is being removed."""
        for selector in list(self._pulse_timers):
            timer = self._pulse_timers.pop(selector, None)
            if timer is not None:
                with contextlib.suppress(Exception):
                    timer.stop()

    async def _load_thread_name(self) -> None:
        """Populate the toolbar thread-name input from server metadata."""
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
        if not _PULSE_ENABLED:
            return
        if busy:
            self._stop_pulse("#prompt")
            self._start_pulse(
                "#transcript",
                peak_var=_BUSY_PULSE_PEAK,
                duration=_BUSY_PULSE_DURATION,
            )
        else:
            self._stop_pulse("#transcript")
            self._start_pulse(
                "#prompt",
                peak_var=_IDLE_PULSE_PEAK,
                duration=_IDLE_PULSE_DURATION,
            )

    def _resolve_css_color(self, var_name: str, fallback: str) -> Color:
        """Resolve a theme variable (e.g. ``accent``) to a concrete Color.

        ``var_name`` is the bare variable name without the leading ``$``.
        Falls back to ``fallback`` (a hex string) if the variable is
        not present — keeps the pulse from crashing under unusual
        themes.
        """
        variables = self.get_css_variables()
        raw = variables.get(var_name) or fallback
        try:
            return Color.parse(raw)
        except Exception:
            return Color.parse(fallback)

    def _start_pulse(self, selector: str, *, peak_var: str, duration: float) -> None:
        """Begin a breathing border pulse on *selector*.

        Textual does not animate border colors via ``styles.animate``
        (border_*_color is not in ``RenderStyles.ANIMATABLE``), so the
        pulse is a manual timer: every tick interpolates between a dim
        base and a bright peak using a sine wave and writes the result
        to all four border edges.

        ``MONET_CHAT_BORDER_COLOR`` overrides the peak so tmux / multi-pane
        operators can give each chat instance a signature hue.
        """
        if selector in self._pulse_timers:
            return  # already pulsing
        widget = None
        with contextlib.suppress(Exception):
            widget = self.query_one(selector)
        if widget is None:
            return
        base = self._idle_border_color()
        peak = self._pulse_peak_color(peak_var)
        # Track wall-clock start so phase stays continuous across ticks.
        start = asyncio.get_event_loop().time()
        tick_interval = 0.05  # 20fps — smooth enough, cheap enough

        def tick() -> None:
            with contextlib.suppress(Exception):
                self._pulse_tick(widget, base, peak, duration, start)

        self._pulse_timers[selector] = self.set_interval(tick_interval, tick)

    def _idle_border_color(self) -> Color:
        """Dim colour used for inactive borders and as the pulse trough."""
        return self._resolve_css_color(_IDLE_BORDER_VAR, "#1a1a2e")

    def _pulse_peak_color(self, peak_var: str) -> Color:
        """Bright colour at the crest of the pulse.

        ``MONET_CHAT_BORDER_COLOR`` wins when set — the override is the
        point of the env var (tmux pane differentiation).
        """
        if self._border_color_override:
            with contextlib.suppress(Exception):
                return Color.parse(self._border_color_override)
        return self._resolve_css_color(peak_var, "#9b59b6")

    def _apply_idle_borders(self) -> None:
        """Paint both widget borders with the dim idle colour.

        Called on mount so the chat starts with visibly quiet borders —
        the pulse peak then reads as an obvious contrast swing. Also
        called by ``_stop_pulse`` to snap back cleanly.
        """
        base = self._idle_border_color()
        for selector in ("#transcript", "#prompt"):
            widget = None
            with contextlib.suppress(Exception):
                widget = self.query_one(selector)
            if widget is None:
                continue
            current_top = widget.styles.border_top
            border_type = current_top[0] if current_top else "round"
            for edge in ("top", "right", "bottom", "left"):
                setattr(widget.styles, f"border_{edge}", (border_type, base))

    def _pulse_tick(
        self,
        widget: Any,
        base: Color,
        peak: Color,
        duration: float,
        start: float,
    ) -> None:
        """One frame of the pulse loop — interpolate and repaint the border."""
        import math

        elapsed = asyncio.get_event_loop().time() - start
        # Sine wave from 0→1→0 with period = 2 * duration.
        phase = 0.5 - 0.5 * math.cos(math.pi * elapsed / duration)
        color = base.blend(peak, phase)
        current_top = widget.styles.border_top
        border_type = current_top[0] if current_top else "round"
        for edge in ("top", "right", "bottom", "left"):
            setattr(widget.styles, f"border_{edge}", (border_type, color))

    def _stop_pulse(self, selector: str) -> None:
        """Halt a running pulse and snap the border back to the dim idle color."""
        timer = self._pulse_timers.pop(selector, None)
        if timer is not None:
            with contextlib.suppress(Exception):
                timer.stop()
        widget = None
        with contextlib.suppress(Exception):
            widget = self.query_one(selector)
        if widget is None:
            return
        base = self._idle_border_color()
        current_top = widget.styles.border_top
        border_type = current_top[0] if current_top else "round"
        for edge in ("top", "right", "bottom", "left"):
            setattr(widget.styles, f"border_{edge}", (border_type, base))

    def _append_line(self, line: str) -> None:
        """Write *line* to the transcript and buffer it for copy-to-clipboard.

        The plain-text version is kept for the clipboard copy button; the
        rendered version uses :func:`_styled_line` so the leading
        ``[role]`` tag renders in the configured colour.
        """
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
        """Reload the server slash-command list and refresh the suggester."""
        try:
            commands = await self._client.slash_commands()
        except Exception:
            _log.debug("refresh_slash_commands failed", exc_info=True)
            return
        self._server_slash_commands = commands
        self.slash_commands = self._combined_slash_commands()
        self._suggester.update(self.slash_commands)

    # ── Slash-command dropdown ────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter and show the slash-command dropdown as the user types."""
        if event.input.id != "prompt":
            return
        self._refresh_slash_suggest(event.value)

    def _refresh_slash_suggest(self, value: str) -> None:
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
        for cmd in matches[:20]:
            suggest.add_option(Option(cmd, id=cmd))
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
        long-running ``await`` (especially the ``_pending_resume`` future
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
        pending = self._pending_resume
        if pending is not None and not pending.done():
            self._pending_resume = None
            self._append_line(f"[user] {text}")
            pending.set_result(text)
            return
        if self._busy:
            return
        # Detach. Worker reads `_busy` itself; we don't await it here.
        self.run_worker(self._handle_user_text(text), exclusive=False)

    async def _rename_thread(self, name: str) -> None:
        """Persist a thread rename and surface success / failure as a notify."""
        try:
            await self._client.chat.rename_chat(self._chat_thread_id, name)
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
        self._set_toolbar_hints(
            f"press ctrl+c again within {int(_EXIT_CONFIRM_TIMEOUT)}s to exit"
        )
        loop = asyncio.get_running_loop()
        self._exit_arm_handle = loop.call_later(
            _EXIT_CONFIRM_TIMEOUT, self._reset_exit_arm
        )

    def _reset_exit_arm(self) -> None:
        self._exit_arm_handle = None
        self._set_toolbar_hints(_DEFAULT_TOOLBAR_HINTS)

    def _cancel_exit_arm(self) -> None:
        if self._exit_arm_handle is not None:
            self._exit_arm_handle.cancel()
            self._exit_arm_handle = None
        self._set_toolbar_hints(_DEFAULT_TOOLBAR_HINTS)

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
            stream = self._client.chat.send_message(self._chat_thread_id, text)
            await self._run_turn(log, first_stream=stream)
        except Exception as exc:
            self._append_line(f"[error] {exc}")
            _log.exception("chat turn failed")
        self.sub_title = ""
        self._set_busy(False)

    async def _run_turn(
        self,
        log: RichLog,
        first_stream: Any,
    ) -> None:
        """Drive one user turn: stream, handle interrupts, loop until idle."""
        had_output = await self._drain_stream(log, first_stream, source="initial")
        while True:
            pending = await self._client.chat.get_chat_interrupt(self._chat_thread_id)
            if not pending:
                if not had_output:
                    self._append_line("[info] (no assistant response)")
                    _log.warning("turn ended with no output and no interrupt")
                return
            had_output = True  # interrupt form counts as output
            _log.info("interrupt pending tag=%s", pending.get("tag"))
            form = pending.get("values") or {}
            if not isinstance(form, dict) or not form.get("fields"):
                self._append_line("[info] graph paused but no form schema — aborting")
                _log.warning("interrupt payload missing form schema: %r", form)
                return
            decision = await self._collect_resume(form)
            if decision is None:
                self._append_line("[info] interrupt skipped — sending reject")
                decision = {"action": "reject", "feedback": ""}
            _log.info("resume payload=%r", decision)
            stream = self._client.chat.resume_chat(self._chat_thread_id, decision)
            had_output = await self._drain_stream(log, stream, source="resume")

    async def _collect_resume(
        self,
        form: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Render *form* in the transcript and parse the next user reply.

        Loops on parse failure so a typo (``aprove``) becomes a re-prompt
        rather than a silent reject.
        """
        for line in _format_form_prompt(form):
            self._append_line(line)
        first = True
        while True:
            if not first:
                self._append_line(
                    "[error] didn't recognise that — reply: "
                    "approve | revise <feedback> | reject"
                )
            first = False
            # Pause "busy" so the user can submit; spinner stays off
            # until the resume kicks the next stream. HITL waits read
            # as idle so the prompt border pulses to cue "reply here".
            self._set_busy(False)
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            self._pending_resume = future
            with contextlib.suppress(Exception):
                self.query_one("#prompt", Input).focus()
            try:
                text = await future
            finally:
                self._pending_resume = None
            payload = _parse_text_reply(form, text)
            if payload is not None:
                # Re-arm busy state for the resume stream.
                self._set_busy(True)
                return payload

    async def _drain_stream(
        self,
        log: RichLog,
        stream: Any,
        *,
        source: str,
    ) -> bool:
        """Drain *stream*, render events. Returns True when something was shown."""
        streamed = False
        async for chunk in stream:
            if isinstance(chunk, AgentProgress):
                # Progress events are intermediate signal — render them
                # but don't suppress the assistant-fallback below if no
                # actual reply lands.
                line = _format_progress_line(chunk)
                self._append_line(line)
                _log.info(
                    "%s progress agent=%s status=%s",
                    source,
                    chunk.agent_id,
                    chunk.status,
                )
                continue
            self._append_line(f"[assistant] {chunk}")
            _log.info("%s chunk len=%d", source, len(str(chunk)))
            streamed = True
        if streamed:
            return True
        _log.info("%s stream yielded nothing; state read fallback", source)
        try:
            history = await self._client.chat.get_chat_history(self._chat_thread_id)
        except Exception as exc:
            self._append_line(f"[error] state read failed: {exc}")
            _log.exception("get_chat_history failed")
            return True
        for msg in reversed(history):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                content = str(msg.get("content") or "").strip()
                if content:
                    self._append_line(f"[assistant] {content}")
                return True
        return False

    # ── TUI-level slash commands ──────────────────────────────────

    async def _maybe_run_tui_command(self, text: str, log: RichLog) -> bool:
        """Dispatch TUI-local slash commands. Returns True when handled."""
        head, _, rest = text.partition(" ")
        arg = rest.strip()
        if head in {"/new", "/clear"}:
            await self._cmd_new_thread(log)
            return True
        if head == "/threads":
            await self._cmd_list_threads(log)
            return True
        if head == "/switch":
            await self._cmd_switch_thread(log, arg)
            return True
        if head == "/agents":
            await self._cmd_list_agents(log)
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
        self.title = f"monet chat · {new_id}"
        with contextlib.suppress(Exception):
            thread_input = self.query_one("#thread-name", Input)
            thread_input.value = generated_name
            thread_input.placeholder = self._thread_name_placeholder()
        self._reset_transcript(f"[info] new thread · {generated_name} · {new_id[:8]}")

    async def _cmd_list_threads(self, log: RichLog) -> None:
        try:
            chats = await self._client.chat.list_chats()
        except Exception as exc:
            self._append_line(f"[error] /threads failed: {exc}")
            return
        if not chats:
            self._append_line("[info] no chat threads yet")
            return
        options: list[tuple[str, str]] = []
        for c in chats:
            marker = "* " if c.thread_id == self._chat_thread_id else "  "
            name = c.name or "(unnamed)"
            label = f"{marker}{name}  ·  {c.message_count} msgs  ·  {c.thread_id}"
            options.append((c.thread_id, label))

        def _on_pick(result: str | None) -> None:
            if not result:
                return
            self.run_worker(self._switch_thread(result), exclusive=True)

        self.push_screen(_PickerScreen("Select a chat thread", options), _on_pick)

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

    def _reset_transcript(self, first_line: str | None = None) -> None:
        """Clear the transcript RichLog and its copy buffer in lockstep."""
        import contextlib

        self._transcript_lines = []
        with contextlib.suppress(Exception):
            self.query_one("#transcript", RichLog).clear()
        if first_line:
            self._append_line(first_line)

    async def _cmd_list_agents(self, log: RichLog) -> None:
        try:
            caps = await self._client.list_capabilities()
        except Exception as exc:
            self._append_line(f"[error] /agents failed: {exc}")
            return
        if not caps:
            self._append_line("[info] no agents registered on this server")
            return
        options: list[tuple[str, str]] = []
        for cap in sorted(
            caps, key=lambda c: (c.get("agent_id") or "", c.get("command") or "")
        ):
            agent_id = str(cap.get("agent_id") or "")
            command = str(cap.get("command") or "")
            pool = str(cap.get("pool") or "local")
            desc = str(cap.get("description") or "").strip()
            value = f"/{agent_id}:{command}"
            label = f"{value}  ·  pool={pool}"
            if desc:
                label += f"  ·  {desc}"
            options.append((value, label))

        def _on_pick(result: str | None) -> None:
            if result:
                self.prefill_input(result + " ")

        self.push_screen(_PickerScreen("Select an agent command", options), _on_pick)

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
            self._border_color_override = (
                _CUSTOM_BORDER_COLOR or self._profile_border_color
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
            self._border_color_override = value
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
        border = self._border_color_override or "(theme $accent)"
        self._append_line(f"  border      {border}")
        for target, tag in _ROLE_TAGS.items():
            style = self._tag_styles.get(tag, "")
            self._append_line(f"  {target:<11} {style}")
        self._append_line("[info] set via: /colors <target> <colour> | /colors reset")

    def _refresh_active_pulse(self) -> None:
        """Restart whichever pulse is active so a new border colour applies."""
        if not _PULSE_ENABLED:
            return
        for selector in list(self._pulse_timers):
            self._stop_pulse(selector)
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
