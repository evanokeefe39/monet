"""Textual TUI for ``monet chat``.

Thin App shell: compose, reactive state, message routing, key bindings.
All business logic delegated to widgets and command modules.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from monet.cli._namegen import random_chat_name
from monet.cli.chat._commands import CommandContext, dispatch_slash
from monet.cli.chat._constants import (
    EXIT_CONFIRM_TIMEOUT,
    INDICATOR_REFRESH_SECONDS,
    TUI_COMMANDS,
)
from monet.cli.chat._hitl_form import (
    build_hitl_widget,
    build_submit_summary,
    envelope_supports_widgets,
)
from monet.cli.chat._prompt import AutoGrowTextArea

if TYPE_CHECKING:
    from monet.cli.chat._messages import (
        HitlDismissed,
        HitlSubmitted,
        PromptSubmitted,
    )

from monet.cli.chat._screens import (
    AgentsScreen,
    ArtifactsScreen,
    RunsScreen,
    ShortcutsScreen,
    ThreadsScreen,
)
from monet.cli.chat._slash import RegistrySuggester
from monet.cli.chat._status_bar import FocusMode, StatusBar
from monet.cli.chat._themes import MONET_EMBER, MONET_THEMES
from monet.cli.chat._transcript import Transcript
from monet.cli.chat._turn import (
    InterruptCoordinator,
    empty_stream,
    run_turn,
)
from monet.cli.chat._view import DEFAULT_TAG_STYLES

if TYPE_CHECKING:
    from textual.worker import Worker

    from monet.client import MonetClient

_log = logging.getLogger("monet.cli.chat")


class ChatApp(App[None]):
    """Monet chat TUI — thin dispatcher over self-contained widgets."""

    CSS = """
    Screen { background: black; overflow: hidden; }
    * {
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-background: black;
        scrollbar-color: $accent 60%;
        scrollbar-color-hover: $accent;
    }
    #main-body { height: 100%; layers: base overlay; }
    #slash-suggest {
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 8;
        margin-bottom: 5;
        border: solid $accent;
        background: black;
        display: none;
    }
    #slash-suggest.visible { display: block; }
    """

    COMMANDS: ClassVar[set[Any]] = set()
    BINDINGS: ClassVar = [
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+c", "confirm_quit", "Quit", show=False, priority=True),
        Binding("ctrl+x", "cancel_run", "Cancel", show=False),
        Binding("ctrl+space", "focus_prompt", "Prompt", show=False),
        Binding("ctrl+tab", "cycle_focus", "Cycle", show=False),
        Binding("ctrl+1", "open_threads", "Threads", show=False),
        Binding("ctrl+2", "open_agents", "Agents", show=False),
        Binding("ctrl+3", "open_artifacts", "Artifacts", show=False),
        Binding("ctrl+4", "open_runs", "Runs", show=False),
        Binding("ctrl+p", "open_menu", "Menu", show=False, priority=True),
        Binding("ctrl+k", "open_shortcuts", "Shortcuts", show=False),
        Binding("down", "focus_suggest", "Suggestions", show=False),
        Binding("escape", "hide_suggest", "Close", show=False),
        Binding("tab", "accept_suggestion", "Complete", show=False, priority=True),
    ]

    busy: reactive[bool] = reactive(False)
    thread_id: reactive[str] = reactive("")

    def __init__(
        self,
        *,
        client: MonetClient,
        thread_id: str,
        slash_commands: list[str] | None = None,
        history: list[dict[str, Any]] | None = None,
        style: Any = None,
    ) -> None:
        super().__init__()
        self._client: MonetClient = client
        self._initial_thread_id = thread_id
        self._server_slash_commands: list[str] = list(slash_commands or [])
        self.slash_commands: list[str] = self._combined_slash_commands()
        self.slash_descriptions: dict[str, str] = dict(TUI_COMMANDS)
        self._suggester = RegistrySuggester(self.slash_commands)
        self._initial_history = list(history or [])
        self._interrupts = InterruptCoordinator()
        self._turn_worker: Worker[None] | None = None
        self._exit_arm_handle: asyncio.TimerHandle | None = None
        self._focus_mode: FocusMode = FocusMode.INPUT
        self._tag_styles: dict[str, str] = dict(DEFAULT_TAG_STYLES)
        self._welcome_shown = False
        self._crash_error: BaseException | None = None

    def _combined_slash_commands(self) -> list[str]:
        out: list[str] = [cmd for cmd, _ in TUI_COMMANDS]
        seen = set(out)
        for cmd in self._server_slash_commands:
            if cmd not in seen:
                out.append(cmd)
                seen.add(cmd)
        return out

    # ── Compose ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="main-body"):
            yield Transcript(tag_styles=self._tag_styles, id="transcript")
            yield OptionList(id="slash-suggest")
            yield AutoGrowTextArea(id="prompt")
            yield StatusBar(id="status-bar")

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_mount(self) -> None:
        for theme in MONET_THEMES:
            with contextlib.suppress(Exception):
                self.register_theme(theme)
        self.theme = MONET_EMBER.name

        self.thread_id = self._initial_thread_id
        self.title = (
            f"monet chat · {self._initial_thread_id}"
            if self._initial_thread_id
            else "monet chat · (new)"
        )

        transcript = self.query_one("#transcript", Transcript)
        for msg in self._initial_history:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            transcript.append(f"[{role}] {content}", markdown=(role == "assistant"))

        if not self._initial_history:
            self.call_after_refresh(self._show_welcome)
        else:
            self.query_one("#prompt", AutoGrowTextArea).focus()

        self.run_worker(self._refresh_slash_commands(), exclusive=False)
        self.set_interval(INDICATOR_REFRESH_SECONDS, self._refresh_indicator)
        if self._initial_thread_id:
            self.run_worker(self._recover_pending_interrupt(), exclusive=False)

    def _show_welcome(self) -> None:
        self._welcome_shown = True
        try:
            transcript = self.query_one("#transcript", Transcript)
            transcript.show_welcome()
            self.query_one("#status-bar", StatusBar).display = False
        except Exception:
            pass

    # ── Reactive watchers ────────────────────────────────────────────

    def watch_busy(self, busy: bool) -> None:
        status = self.query_one("#status-bar", StatusBar)
        if busy:
            status.update_segments(
                active_run=self.thread_id[:8] if self.thread_id else "..."
            )
        else:
            status.update_segments(active_run="")

    # ── Message handlers ─────────────────────────────────────────────

    def dismiss_welcome(self) -> None:
        """Called by WelcomeOverlay when any key is pressed."""
        self._welcome_shown = False
        self.query_one("#status-bar", StatusBar).display = True
        self.query_one("#prompt", AutoGrowTextArea).focus()

    def on_prompt_submitted(self, event: PromptSubmitted) -> None:
        text = event.text.strip()
        if not text:
            return
        if text in {"/quit", "/exit"}:
            self.exit()
            return
        if text == "/threads":
            self.action_open_threads()
            return
        if text == "/agents":
            self.action_open_agents()
            return
        if text == "/artifacts":
            self.action_open_artifacts()
            return
        if text == "/runs":
            self.action_open_runs()
            return
        if self._interrupts.is_pending():
            self._transcript.append(f"[user] {text}")
            self._interrupts.consume_if_pending(text)
            return
        if self.busy:
            return
        self._turn_worker = self.run_worker(
            self._handle_user_text(text), exclusive=False
        )

    def on_hitl_submitted(self, msg: HitlSubmitted) -> None:
        envelope = self._hitl_envelope
        if envelope is None:
            return
        if msg.payload is None:
            self.notify("please fill in every required field", severity="warning")
            return
        summary = build_submit_summary(envelope, msg.payload)
        self._transcript.append(f"[user] {summary}")
        self._interrupts.consume_payload(msg.payload)

    def on_hitl_dismissed(self, msg: HitlDismissed) -> None:
        self._transcript.unmount_hitl()
        self._transcript.append("[info] interrupt dismissed")

    # ── Slash suggest dropdown ───────────────────────────────────────

    def on_text_area_changed(self, event: AutoGrowTextArea.Changed) -> None:
        if event.text_area.id != "prompt":
            return
        self._refresh_slash_suggest(event.text_area.text)

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
        self.query_one("#prompt", AutoGrowTextArea).focus()

    async def action_accept_suggestion(self) -> None:
        prompt = self.query_one("#prompt", AutoGrowTextArea)
        if not prompt.has_focus:
            self.screen.focus_next()
            return
        suggestion = await self._suggester.get_suggestion(prompt.text)
        if not suggestion or suggestion == prompt.text:
            self.screen.focus_next()
            return
        prompt.text = suggestion

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "slash-suggest":
            return
        chosen = str(event.option.id or "")
        if not chosen:
            return
        prompt = self.query_one("#prompt", AutoGrowTextArea)
        prompt.text = chosen + " "
        self.query_one("#slash-suggest", OptionList).remove_class("visible")
        prompt.focus()

    # ── Actions ──────────────────────────────────────────────────────

    def action_focus_prompt(self) -> None:
        self.query_one("#prompt", AutoGrowTextArea).focus()
        self._focus_mode = FocusMode.INPUT
        self._status_bar.set_focus(FocusMode.INPUT)

    def action_cycle_focus(self) -> None:
        if self._focus_mode == FocusMode.INPUT:
            self._focus_mode = FocusMode.TRANSCRIPT
            self.query_one("#transcript", Transcript).query_one("#_log").focus()
        else:
            self._focus_mode = FocusMode.INPUT
            self.query_one("#prompt", AutoGrowTextArea).focus()
        self._status_bar.set_focus(self._focus_mode)

    def action_confirm_quit(self) -> None:
        if self._exit_arm_handle is not None:
            self._cancel_exit_arm()
            self.exit()
            return
        self._status_bar.set_override(
            f"press ctrl+c again within {int(EXIT_CONFIRM_TIMEOUT)}s to exit"
        )
        loop = asyncio.get_running_loop()
        self._exit_arm_handle = loop.call_later(
            EXIT_CONFIRM_TIMEOUT, self._reset_exit_arm
        )

    def _reset_exit_arm(self) -> None:
        self._exit_arm_handle = None
        self._status_bar.clear_override()

    def _cancel_exit_arm(self) -> None:
        if self._exit_arm_handle is not None:
            self._exit_arm_handle.cancel()
            self._exit_arm_handle = None
        self._status_bar.clear_override()

    def action_cancel_run(self) -> None:
        if self._turn_worker is not None:
            self._turn_worker.cancel()
            self._turn_worker = None
        self.busy = False
        self._transcript.append("[info] run cancelled")

    def action_open_threads(self) -> None:
        def _on_pick(result: str | None) -> None:
            if result:
                self.run_worker(self._switch_thread(result), exclusive=True)

        self.push_screen(ThreadsScreen(self._client, self.thread_id), _on_pick)

    def action_open_agents(self) -> None:
        def _on_pick(result: str | None) -> None:
            if result:
                self.prefill_input(result + " ")

        self.push_screen(AgentsScreen(self._client), _on_pick)

    def action_open_artifacts(self) -> None:
        if not self.thread_id:
            self._transcript.append("[info] no active thread for artifacts")
            return

        def _on_pick(result: str | None) -> None:
            if result:
                self._copy_artifact_url(result)

        self.push_screen(ArtifactsScreen(self._client, self.thread_id), _on_pick)

    def action_open_runs(self) -> None:
        self.push_screen(RunsScreen(self._client, self.thread_id), lambda _: None)

    def action_open_shortcuts(self) -> None:
        self.push_screen(ShortcutsScreen())

    def action_open_menu(self) -> None:
        self.push_screen(ShortcutsScreen())

    # ── Properties ───────────────────────────────────────────────────

    @property
    def _transcript(self) -> Transcript:
        return self.query_one("#transcript", Transcript)

    @property
    def _status_bar(self) -> StatusBar:
        return self.query_one("#status-bar", StatusBar)

    # ── Public hooks ─────────────────────────────────────────────────

    def prefill_input(self, text: str) -> None:
        prompt = self.query_one("#prompt", AutoGrowTextArea)
        prompt.text = text
        prompt.focus()

    # ── Turn execution ───────────────────────────────────────────────

    async def _handle_user_text(self, text: str) -> None:
        transcript = self._transcript
        transcript.append(f"[user] {text}")

        cmd_ctx = self._make_command_context()
        if await dispatch_slash(cmd_ctx, text):
            return

        self.busy = True
        transcript.append("[info] thinking...")
        try:
            thread_id = await self._ensure_thread_id()
            stream = self._client.chat.send_message(thread_id, text)
            await run_turn(
                client=self._client,
                thread_id=thread_id,
                first_stream=stream,
                coordinator=self._interrupts,
                writer=self._writer,
                busy_setter=self._set_busy,
                focus_prompt=self._focus_prompt,
                get_interrupt=self._client.chat.get_chat_interrupt,
                resume=self._client.chat.resume_chat,
                mount_widgets=self._mount_hitl_widgets,
                unmount_widgets=self._unmount_hitl_widgets,
            )
        except Exception as exc:
            transcript.append(f"[error] {exc}")
            _log.exception("chat turn failed")
            await self._recover_pending_interrupt()
        finally:
            self.busy = False
            self._turn_worker = None

    def _writer(self, line: str) -> None:
        markdown = line.startswith("[assistant]")
        self._transcript.append(line, markdown=markdown)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy

    def _focus_prompt(self) -> None:
        self.query_one("#prompt", AutoGrowTextArea).focus()
        self._focus_mode = FocusMode.INPUT
        self._status_bar.set_focus(FocusMode.INPUT)

    async def _ensure_thread_id(self) -> str:
        if self.thread_id:
            return self.thread_id
        name = random_chat_name()
        new_id = await self._client.chat.create_chat(name=name)
        self.thread_id = new_id
        self.title = f"monet chat · {name}"
        self._status_bar.update_segments(thread_name=name)
        return new_id

    # ── HITL widgets ─────────────────────────────────────────────────

    _hitl_envelope: Any = None

    def _mount_hitl_widgets(self, form: dict[str, Any]) -> bool:
        from monet.types import InterruptEnvelope

        envelope = InterruptEnvelope.from_interrupt_values(form)
        if envelope is None or not envelope_supports_widgets(envelope):
            return False
        try:
            widget = build_hitl_widget(envelope, self._on_hitl_payload)
            self._transcript.mount_hitl(widget)
        except Exception:
            _log.exception("mount hitl widgets failed")
            return False
        self._hitl_envelope = envelope
        if envelope.prompt:
            self._transcript.append(f"[info] {envelope.prompt}")
        self._transcript.append("[info] select an option, or type a reply")
        return True

    def _unmount_hitl_widgets(self) -> None:
        self._hitl_envelope = None
        self._transcript.unmount_hitl()

    def _on_hitl_payload(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            self.notify("please fill in every required field", severity="warning")
            return
        envelope = self._hitl_envelope
        if envelope is not None:
            summary = build_submit_summary(envelope, payload)
            self._transcript.append(f"[user] {summary}")
        self._interrupts.consume_payload(payload)

    # ── Interrupt recovery ───────────────────────────────────────────

    async def _recover_pending_interrupt(self) -> None:
        if not self.thread_id:
            return
        try:
            pending = await self._client.chat.get_chat_interrupt(self.thread_id)
        except Exception:
            return
        if not pending:
            return
        self._transcript.append("[info] pending approval found — resuming")
        self.busy = True
        try:
            await run_turn(
                client=self._client,
                thread_id=self.thread_id,
                first_stream=empty_stream(),
                coordinator=self._interrupts,
                writer=self._writer,
                busy_setter=self._set_busy,
                focus_prompt=self._focus_prompt,
                get_interrupt=self._client.chat.get_chat_interrupt,
                resume=self._client.chat.resume_chat,
                mount_widgets=self._mount_hitl_widgets,
                unmount_widgets=self._unmount_hitl_widgets,
            )
        except Exception as exc:
            self._transcript.append(f"[error] {exc}")
            _log.exception("interrupt recovery failed")
        self.busy = False

    # ── Thread switching ─────────────────────────────────────────────

    async def _switch_thread(self, target: str) -> None:
        cmd_ctx = self._make_command_context()
        from monet.cli.chat._commands import _cmd_switch

        await _cmd_switch(cmd_ctx, target)
        self.thread_id = target
        self.run_worker(self._recover_pending_interrupt(), exclusive=False)

    # ── Background tasks ─────────────────────────────────────────────

    async def _refresh_slash_commands(self) -> None:
        try:
            commands = await self._client.slash_commands()
        except Exception:
            return
        self._server_slash_commands = commands
        self.slash_commands = self._combined_slash_commands()
        self._suggester.update(self.slash_commands)
        descriptions: dict[str, str] = dict(TUI_COMMANDS)
        try:
            caps = await self._client.list_capabilities()
        except Exception:
            caps = []
        for cap in caps:
            agent_id = str(cap.get("agent_id") or "")
            command = str(cap.get("command") or "")
            desc = str(cap.get("description") or "").strip()
            if agent_id and command and desc:
                descriptions[f"/{agent_id}:{command}"] = desc
        self.slash_descriptions = descriptions

    def _refresh_indicator(self) -> None:
        self.run_worker(self._refresh_indicator_async(), exclusive=False)

    async def _refresh_indicator_async(self) -> None:
        agents = 0
        try:
            caps = await self._client.list_capabilities()
            agents = len(caps)
        except Exception:
            pass
        self._status_bar.update_segments(agents=agents)

    # ── Helpers ──────────────────────────────────────────────────────

    def _copy_artifact_url(self, artifact_id: str) -> None:
        base = getattr(self._client, "_url", "") or ""
        url = f"{base.rstrip('/')}/api/v1/artifacts/{artifact_id}/view"
        try:
            self.copy_to_clipboard(url)
            self.notify(f"copied artifact url · {artifact_id[:8]}")
        except Exception as exc:
            _log.warning("copy failed: %s", exc)

    def _make_command_context(self) -> CommandContext:
        return CommandContext(
            client=self._client,
            transcript=self._transcript,
            thread_id=self.thread_id,
            server_slash_commands=self._server_slash_commands,
            get_thread_id=lambda: self.thread_id,
            set_thread_id=self._set_thread_id,
            set_title=self._set_title,
            update_status=self._status_bar.update_segments,
            show_welcome=self._show_welcome,
        )

    def _set_thread_id(self, tid: str) -> None:
        self.thread_id = tid

    def _set_title(self, title: str) -> None:
        self.title = title

    async def _collect_resume(self, form: dict[str, Any]) -> dict[str, Any] | None:
        """Collect a HITL resume payload. Used by tests."""
        return await self._interrupts.collect(
            form,
            writer=self._writer,
            busy_setter=self._set_busy,
            focus_prompt=self._focus_prompt,
            mount_widgets=self._mount_hitl_widgets,
            unmount_widgets=self._unmount_hitl_widgets,
        )
