"""Textual TUI for ``monet chat``.

Replaces the :func:`click.prompt`-based REPL with a richer terminal UI:

- :class:`RichLog` transcript with markdown support for assistant replies.
- :class:`Input` prompt wired to :class:`RegistrySuggester` for ghost-text
  slash-command completion.
- A :class:`SlashCommandProvider` registered with the built-in command
  palette (``ctrl+p``) so users can browse the live registry.
- :class:`InterruptScreen` — a modal that walks the form-schema envelope
  (``Form`` / ``Field`` from :mod:`monet.client._events`) and maps each
  ``FieldType`` to a Textual widget for dynamic HITL rendering.

The app is driven by a :class:`~monet.client.MonetClient`; the Click
entry point in :mod:`monet.cli._chat` resolves the thread, builds the
client, and calls :meth:`ChatApp.run_async`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.suggester import Suggester
from textual.widgets import (
    Button,
    Checkbox,
    Header,
    Input,
    Label,
    OptionList,
    RadioButton,
    RadioSet,
    RichLog,
    Select,
    SelectionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

_log = logging.getLogger("monet.cli.chat")


#: Per-role styles for transcript tag highlighting.
_TAG_STYLES: dict[str, str] = {
    "[user]": "bold #3b82f6",  # high-contrast bright blue
    "[assistant]": "bold #a855f7",  # purple
    "[info]": "bold #9ca3af",  # light grey
    "[error]": "bold red",
}


def _styled_line(line: str) -> Text:
    """Return a ``rich.Text`` with the leading tag coloured per ``_TAG_STYLES``."""
    for tag, style in _TAG_STYLES.items():
        if line.startswith(tag):
            rest = line[len(tag) :]
            text = Text()
            text.append(tag, style=style)
            text.append(rest)
            return text
    return Text(line)


if TYPE_CHECKING:
    from monet.client import MonetClient
    from monet.client._events import Field, Form


#: Slash commands handled by the TUI itself (not forwarded to the server).
TUI_COMMANDS: tuple[str, ...] = (
    "/new",
    "/clear",
    "/threads",
    "/switch",
    "/agents",
    "/help",
    "/quit",
    "/exit",
)


# --- Slash-command completion ---------------------------------------------


class RegistrySuggester(Suggester):
    """Ghost-text suggester backed by a live slash-command list.

    The list is expected to already include reserved prefixes like
    ``/plan`` plus ``/<agent>:<command>`` derived from the server
    manifest. Passing the list once at construction keeps the suggester
    synchronous — the Textual :class:`Input` widget calls
    :meth:`get_suggestion` on every keystroke.
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


# --- Interrupt (HITL) form screen -----------------------------------------


class InterruptScreen(Screen[dict[str, Any]]):
    """Full-page screen that renders a :class:`Form` as interactive widgets.

    ``FieldType`` → widget mapping:

    * ``text`` → :class:`Input`
    * ``textarea`` → :class:`TextArea`
    * ``radio`` → :class:`RadioSet`
    * ``checkbox`` with ``options`` → :class:`SelectionList`
    * ``checkbox`` without ``options`` → single :class:`Checkbox`
    * ``select`` → :class:`Select`
    * ``int`` → :class:`Input` with integer validation
    * ``bool`` → :class:`Checkbox`
    * ``hidden`` → not rendered; default value carried into the submission

    Submission returns ``{field.name: value}`` via
    :meth:`ModalScreen.dismiss` — the caller feeds that dict into
    :meth:`MonetClient.resume`.
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    InterruptScreen {
        padding: 1 2;
    }

    InterruptScreen .interrupt-prompt {
        padding-bottom: 1;
        text-style: bold;
    }

    InterruptScreen #interrupt-body {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
    }

    InterruptScreen .field-label {
        padding-top: 1;
        color: $text-muted;
    }

    InterruptScreen #interrupt-buttons {
        dock: bottom;
        height: 3;
        padding: 1 0 0 0;
    }

    InterruptScreen Button {
        margin: 0 1 0 0;
    }
    """

    def __init__(self, form: Form) -> None:
        super().__init__()
        self._form: Form = form
        self._fields: list[Field] = list(form.get("fields") or [])
        self._widget_index: dict[str, Any] = {}
        self._hidden_defaults: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        prompt = str(self._form.get("prompt") or "Please respond:")
        yield Static(prompt, classes="interrupt-prompt")
        with VerticalScroll(id="interrupt-body"):
            for field in self._fields:
                widget = self._compose_field(field)
                if widget is not None:
                    yield widget
        with Horizontal(id="interrupt-buttons"):
            yield Button("Submit", id="submit", variant="primary")
            yield Button("Cancel", id="cancel")

    def _compose_field(self, field: Field) -> Any:
        name = str(field.get("name") or "")
        if not name:
            return None
        ftype = str(field.get("type") or "text")
        label = str(field.get("label") or name)
        default = field.get("default")
        options = field.get("options") or []

        if ftype == "hidden":
            self._hidden_defaults[name] = default
            return None

        block = Vertical()
        header = Label(label, classes="field-label")

        if ftype == "text":
            widget: Any = Input(value=str(default or ""), id=f"f-{name}")
        elif ftype == "textarea":
            widget = TextArea(str(default or ""), id=f"f-{name}")
        elif ftype == "int":
            widget = Input(
                value=str(default if default is not None else ""),
                type="integer",
                id=f"f-{name}",
            )
        elif ftype == "bool":
            widget = Checkbox(label, value=bool(default), id=f"f-{name}")
            # Checkbox already carries its own label — collapse the header.
            self._widget_index[name] = widget
            return widget
        elif ftype == "radio":
            buttons = [
                RadioButton(
                    str(_option_label(o)),
                    value=(_option_value(o) == default),
                    id=f"r-{name}-{idx}",
                    name=str(_option_value(o)),
                )
                for idx, o in enumerate(options)
            ]
            widget = RadioSet(*buttons, id=f"f-{name}")
        elif ftype == "checkbox":
            if options:
                selection_items = [
                    (
                        str(_option_label(o)),
                        _option_value(o),
                        _is_selected(_option_value(o), default),
                    )
                    for o in options
                ]
                widget = SelectionList(*selection_items, id=f"f-{name}")
            else:
                widget = Checkbox(label, value=bool(default), id=f"f-{name}")
                self._widget_index[name] = widget
                return widget
        elif ftype == "select":
            choices = [(str(_option_label(o)), _option_value(o)) for o in options]
            widget = Select(choices, id=f"f-{name}", allow_blank=False)
            if default is not None:
                for _, value in choices:
                    if value == default:
                        widget.value = value
                        break
        else:
            widget = Input(value=str(default or ""), id=f"f-{name}")

        block.compose_add_child(header)
        block.compose_add_child(widget)
        self._widget_index[name] = widget
        return block

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.dismiss(self._collect())
        elif event.button.id == "cancel":
            self.dismiss({})

    def action_cancel(self) -> None:
        self.dismiss({})

    def _collect(self) -> dict[str, Any]:
        """Read every widget back into a ``{name: value}`` dict."""
        out: dict[str, Any] = dict(self._hidden_defaults)
        for name, widget in self._widget_index.items():
            out[name] = _read_widget_value(widget)
        return out


def _option_label(option: Any) -> str:
    if isinstance(option, dict):
        return str(option.get("label") or option.get("value") or "")
    return str(option)


def _option_value(option: Any) -> Any:
    if isinstance(option, dict):
        return option.get("value") or option.get("label")
    return option


def _is_selected(value: Any, default: Any) -> bool:
    if isinstance(default, list):
        return value in default
    return bool(value == default)


def _read_widget_value(widget: Any) -> Any:
    """Return the submission value for a dynamically built field widget."""
    if isinstance(widget, Checkbox):
        return widget.value
    if isinstance(widget, Input):
        if widget.type == "integer":
            raw = widget.value.strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                return raw
        return widget.value
    if isinstance(widget, TextArea):
        return widget.text
    if isinstance(widget, RadioSet):
        pressed = widget.pressed_button
        return pressed.name if pressed is not None else None
    if isinstance(widget, SelectionList):
        return list(widget.selected)
    if isinstance(widget, Select):
        return widget.value
    return None


# --- List-picker screens (threads + agents) ------------------------------


class _PickerScreen(Screen[str | None]):
    """Full-screen list picker — arrow keys nav, Enter select, Esc back."""

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Back", show=False),
        Binding("shift+tab", "cancel", "Back", show=False),
    ]

    DEFAULT_CSS = """
    _PickerScreen {
        padding: 1 2;
    }

    _PickerScreen .picker-title {
        text-style: bold;
        padding-bottom: 1;
    }

    _PickerScreen OptionList {
        height: 1fr;
        border: round $primary;
    }

    _PickerScreen .picker-hint {
        color: $text-muted;
        padding-top: 1;
    }
    """

    def __init__(self, title: str, options: list[tuple[str, str]]) -> None:
        """``options`` is ``[(value, display_label), …]``."""
        super().__init__()
        self._picker_title = title
        self._options = options

    def compose(self) -> ComposeResult:
        yield Static(self._picker_title, classes="picker-title")
        yield OptionList(
            *(Option(label, id=value) for value, label in self._options),
            id="picker",
        )
        yield Static(
            "↑/↓ navigate · enter select · esc back",
            classes="picker-hint",
        )

    def on_mount(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self.query_one(OptionList).focus()

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        self.dismiss(str(event.option.id) if event.option.id else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# --- Main app -------------------------------------------------------------


class ChatApp(App[None]):
    """Textual app wiring :class:`MonetClient` to a live chat REPL."""

    CSS = """
    #toolbar {
        dock: top;
        height: 1;
        padding: 0 1;
    }

    #toolbar-thread {
        color: $text-muted;
        width: auto;
    }

    #toolbar-hints {
        color: $text-muted;
        width: 1fr;
        content-align: center middle;
    }

    #toolbar Button {
        min-width: 8;
        height: 1;
        padding: 0 1;
        margin: 0;
        border: none;
    }

    #transcript {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
    }

    #prompt {
        dock: bottom;
        height: 5;
        border: round $primary;
        padding: 0 1;
        margin: 0;
    }

    #slash-suggest {
        dock: bottom;
        height: auto;
        max-height: 8;
        margin: 0 0 5 0;
        border: round $accent;
        background: $panel;
        display: none;
    }

    #slash-suggest.visible {
        display: block;
    }
    """

    COMMANDS: ClassVar = App.COMMANDS | {SlashCommandProvider}
    BINDINGS: ClassVar = [
        Binding("ctrl+q", "quit", "Quit", show=False),
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
            yield Static(self._toolbar_thread_text(), id="toolbar-thread")
            yield Static(
                "/new  ·  /threads  ·  /agents  ·  /quit",
                id="toolbar-hints",
            )
            yield Button("⧉ copy", id="copy-transcript", variant="default")
        yield RichLog(id="transcript", wrap=True, markup=False, highlight=False)
        yield OptionList(id="slash-suggest")
        yield Input(
            placeholder="Type a message or /command…",
            id="prompt",
            suggester=self._suggester,
        )

    def _toolbar_thread_text(self) -> str:
        short = self._chat_thread_id[:8] if self._chat_thread_id else "(none)"
        return f"thread {short}"

    def _refresh_toolbar_thread(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self.query_one("#toolbar-thread", Static).update(
                self._toolbar_thread_text()
            )

    def on_mount(self) -> None:
        self.title = f"monet chat · {self._chat_thread_id}"
        self._refresh_toolbar_thread()
        for msg in self._initial_history:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            self._append_line(f"[{role}] {content}")
        self.query_one("#prompt", Input).focus()

    def _append_line(self, line: str) -> None:
        """Write *line* to the transcript and buffer it for copy-to-clipboard.

        The plain-text version is kept for the clipboard copy button; the
        rendered version uses :func:`_styled_line` so the leading
        ``[role]`` tag renders in the configured colour.
        """
        import contextlib

        self._transcript_lines.append(line)
        with contextlib.suppress(Exception):
            self.query_one("#transcript", RichLog).write(_styled_line(line))

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
        if self._busy:
            return
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        log = self.query_one("#transcript", RichLog)
        self._append_line(f"[user] {text}")
        _log.info("user submit thread=%s text=%r", self._chat_thread_id, text)
        if text in {"/quit", "/exit"}:
            self.exit()
            return
        if await self._maybe_run_tui_command(text, log):
            return
        self._busy = True
        self.sub_title = "thinking…"
        self._append_line("[info] thinking…")
        try:
            await self._run_turn(
                log, first_stream=self._client.send_message(self._chat_thread_id, text)
            )
        except Exception as exc:
            self._append_line(f"[error] {exc}")
            _log.exception("chat turn failed")
        self.sub_title = ""
        self._busy = False

    async def _run_turn(
        self,
        log: RichLog,
        first_stream: Any,
    ) -> None:
        """Drive one user turn: stream, handle interrupts, loop until idle."""
        await self._drain_stream(log, first_stream, source="initial")
        while True:
            pending = await self._client.get_chat_interrupt(self._chat_thread_id)
            if not pending:
                return
            _log.info("interrupt pending tag=%s", pending.get("tag"))
            form = pending.get("values") or {}
            if not isinstance(form, dict) or not form.get("fields"):
                self._append_line("[info] graph paused but no form schema — aborting")
                _log.warning("interrupt payload missing form schema: %r", form)
                return
            decision = await self._push_interrupt_screen(form)
            if not decision:
                self._append_line("[info] (cancelled; sending reject)")
                decision = {"action": "reject"}
            _log.info("resume payload=%r", decision)
            stream = self._client.resume_chat(self._chat_thread_id, decision)
            await self._drain_stream(log, stream, source="resume")

    async def _drain_stream(
        self,
        log: RichLog,
        stream: Any,
        *,
        source: str,
    ) -> None:
        streamed = False
        async for chunk in stream:
            self._append_line(f"[assistant] {chunk}")
            _log.info("%s chunk len=%d", source, len(str(chunk)))
            streamed = True
        if streamed:
            return
        _log.info("%s stream yielded nothing; state read fallback", source)
        try:
            history = await self._client.get_chat_history(self._chat_thread_id)
        except Exception as exc:
            self._append_line(f"[error] state read failed: {exc}")
            _log.exception("get_chat_history failed")
            return
        for msg in reversed(history):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                content = str(msg.get("content") or "").strip()
                if content:
                    self._append_line(f"[assistant] {content}")
                return
        self._append_line("[info] (no assistant response)")
        _log.warning("%s fallback found no assistant message", source)

    async def _push_interrupt_screen(
        self,
        form: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Push an InterruptScreen and await its result via a callback.

        :meth:`push_screen_wait` requires a worker context; using a
        callback + :class:`asyncio.Event` keeps the caller free of the
        ``@work`` decorator machinery and works from event handlers.
        """
        done: asyncio.Event = asyncio.Event()
        holder: dict[str, Any] = {"result": None}

        def _cb(result: dict[str, Any] | None) -> None:
            holder["result"] = result
            done.set()

        self.push_screen(InterruptScreen(form), _cb)  # type: ignore[arg-type]
        await done.wait()
        result = holder["result"]
        if result is None or isinstance(result, dict):
            return result
        return None

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
        if head == "/help":
            self._cmd_help(log)
            return True
        return False

    async def _cmd_new_thread(self, log: RichLog) -> None:
        try:
            new_id = await self._client.create_chat()
        except Exception as exc:
            self._append_line(f"[error] /new failed: {exc}")
            return
        self._chat_thread_id = new_id
        self.title = f"monet chat · {new_id}"
        self._refresh_toolbar_thread()
        self._reset_transcript(f"[info] new thread · {new_id}")

    async def _cmd_list_threads(self, log: RichLog) -> None:
        try:
            chats = await self._client.list_chats()
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
            history = await self._client.get_chat_history(target)
        except Exception as exc:
            self._append_line(f"[error] /switch failed: {exc}")
            return
        self._chat_thread_id = target
        self.title = f"monet chat · {target}"
        self._refresh_toolbar_thread()
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

    def _cmd_help(self, log: RichLog) -> None:
        self._append_line("[info] TUI commands:")
        self._append_line("  /new, /clear        start a fresh thread")
        self._append_line("  /threads            open the thread picker")
        self._append_line("  /switch <thread>    resume an existing thread by id")
        self._append_line("  /agents             open the agent-command picker")
        self._append_line("  /quit, /exit        leave the REPL")
        self._append_line("[info] server-side slash commands:")
        for cmd in self._server_slash_commands[:20]:
            self._append_line(f"  {cmd} <task>")
