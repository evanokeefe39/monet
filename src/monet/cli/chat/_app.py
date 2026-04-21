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
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from monet.cli._namegen import random_chat_name
from monet.cli.chat._commands import CommandContext, dispatch_slash
from monet.cli.chat._constants import (
    INDICATOR_REFRESH_SECONDS,
    SLASH_SUGGEST_DEBOUNCE,
    SLASH_SUGGEST_MAX_OPTIONS,
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
from monet.cli.chat._status_bar import StatusBar
from monet.cli.chat._themes import MONET_EMBER, MONET_THEMES
from monet.cli.chat._transcript import Transcript
from monet.cli.chat._turn import (
    InterruptCoordinator,
    empty_stream,
    run_turn,
)
from monet.cli.chat._view import DEFAULT_TAG_STYLES

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.timer import Timer
    from textual.worker import Worker

    from monet.client import MonetClient

_log = logging.getLogger("monet.cli.chat")
_V = MONET_EMBER.variables

_PROMPT_ACCENT = _V["teal-600"]


class ChatApp(App[None]):
    """Monet chat TUI — thin dispatcher over self-contained widgets."""

    CSS = f"""
    Screen {{ background: black; overflow: hidden; }}
    * {{
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-background: black;
        scrollbar-color: $accent 60%;
        scrollbar-color-hover: $accent;
    }}
    #main-body {{ height: 100%; layers: base overlay; }}
    #slash-suggest {{
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 8;
        margin-bottom: 5;
        border: solid {_PROMPT_ACCENT};
        background: black;
        display: none;
    }}
    #slash-suggest.visible {{ display: block; }}
    #prompt-area {{
        dock: bottom;
        height: 3;
        max-height: 8;
        margin-bottom: 1;
        border: solid $surface;
        background: black;
        padding: 0;
    }}
    #prompt-area:focus-within {{
        border: solid {_PROMPT_ACCENT};
    }}
    #prompt-glyph {{
        width: 2;
        height: 1;
        padding: 0;
        color: {_PROMPT_ACCENT};
        background: black;
    }}
    """

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
        self._thread_progress: dict[str, list[str]] = {}
        self._nav_state: str = "prompt"
        self._tag_styles: dict[str, str] = dict(DEFAULT_TAG_STYLES)
        self._welcome_shown = False
        self._crash_error: BaseException | None = None
        self._slash_timer: Timer | None = None
        self._last_slash_prefix: str = ""
        self._last_nav_override: str = ""
        self._cached_slash_suggest: OptionList | None = None
        self._cached_prompt: AutoGrowTextArea | None = None
        self._cached_transcript: Transcript | None = None
        self._cached_status_bar: StatusBar | None = None

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
            with Horizontal(id="prompt-area"):
                yield Static("> ", id="prompt-glyph")
                yield AutoGrowTextArea(id="prompt")
            yield StatusBar(id="status-bar")

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._cached_slash_suggest = self.query_one("#slash-suggest", OptionList)
        self._cached_prompt = self.query_one("#prompt", AutoGrowTextArea)
        self._cached_transcript = self.query_one("#transcript", Transcript)
        self._cached_status_bar = self.query_one("#status-bar", StatusBar)

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

        transcript = self._transcript
        for msg in self._initial_history:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            transcript.append(f"[{role}] {content}", markdown=(role == "assistant"))

        if not self._initial_history:
            self.call_after_refresh(self._show_welcome)
        else:
            self._cached_prompt.focus()

        self.run_worker(self._refresh_slash_commands(), exclusive=False)
        self.set_interval(INDICATOR_REFRESH_SECONDS, self._refresh_indicator)
        if self._initial_thread_id:
            self.run_worker(self._load_thread_name(), exclusive=False)
            self.run_worker(self._recover_pending_interrupt(), exclusive=False)

    def _show_welcome(self) -> None:
        self._welcome_shown = True
        try:
            self._transcript.show_welcome()
            self._status_bar.display = False
            self.query_one("#prompt-area").display = False
        except Exception:
            pass

    # ── Reactive watchers ────────────────────────────────────────────

    def watch_busy(self, busy: bool) -> None:
        sb = self._status_bar
        if busy:
            sb.update_segments(
                active_run=self.thread_id[:8] if self.thread_id else "..."
            )
        else:
            sb.update_segments(active_run="")

    def watch_thread_id(self, tid: str) -> None:
        """Called by Textual reactive when thread_id changes."""
        if tid:
            self._refresh_indicator()

    # ── Message handlers ─────────────────────────────────────────────

    def dismiss_welcome(self) -> None:
        """Called by WelcomeOverlay when any key is pressed."""
        self._welcome_shown = False
        self._status_bar.display = True
        self.query_one("#prompt-area").display = True
        self._cached_prompt.focus() if self._cached_prompt else None

    def on_prompt_submitted(self, event: PromptSubmitted) -> None:
        if self._welcome_shown:
            return
        suggest = self._cached_slash_suggest
        prompt = self._cached_prompt
        if self._suggest_visible() and suggest is not None:
            opt = suggest.get_option_at_index(suggest.highlighted or 0)
            if opt and opt.id and prompt is not None:
                prompt.text = str(opt.id) + " "
            suggest.remove_class("visible")
            return
        text = event.text.strip()
        if not text:
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
        suggest = self._cached_slash_suggest
        stripped = event.text_area.text.strip()
        if not stripped.startswith("/") or " " in stripped:
            if self._slash_timer is not None:
                self._slash_timer.stop()
                self._slash_timer = None
            if suggest is not None:
                suggest.remove_class("visible")
            self._last_slash_prefix = ""
            self._update_nav_hint()
            return
        if self._slash_timer is not None:
            self._slash_timer.stop()
        self._slash_timer = self.set_timer(
            SLASH_SUGGEST_DEBOUNCE, self._do_slash_suggest
        )

    def _do_slash_suggest(self) -> None:
        self._slash_timer = None
        prompt = self._cached_prompt
        if prompt is not None:
            self._refresh_slash_suggest(prompt.text)

    def _refresh_slash_suggest(self, value: str) -> None:
        suggest = self._cached_slash_suggest
        if suggest is None:
            return
        stripped = value.strip()
        if stripped == self._last_slash_prefix:
            return
        self._last_slash_prefix = stripped
        if not stripped.startswith("/") or " " in stripped:
            suggest.remove_class("visible")
            if self._nav_state == "suggest":
                self._set_nav("prompt")
            return
        matches = [cmd for cmd in self.slash_commands if cmd.startswith(stripped)]
        suggest.clear_options()
        if not matches:
            suggest.remove_class("visible")
            if self._nav_state == "suggest":
                self._set_nav("prompt")
            return
        width = max(len(cmd) for cmd in matches[:SLASH_SUGGEST_MAX_OPTIONS])
        for cmd in matches[:SLASH_SUGGEST_MAX_OPTIONS]:
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append(f"{cmd:<{width}}", style="bold")
            desc = self.slash_descriptions.get(cmd, "")
            if desc:
                label.append(f"   {desc}", style="dim")
            suggest.add_option(Option(label, id=cmd))
        suggest.add_class("visible")
        suggest.highlighted = 0
        self._set_nav("suggest")

    def action_focus_suggest(self) -> None:
        suggest = self._cached_slash_suggest
        if suggest is None:
            return
        if "visible" in suggest.classes and suggest.option_count > 0:
            suggest.focus()

    def action_hide_suggest(self) -> None:
        suggest = self._cached_slash_suggest
        if suggest is not None:
            suggest.remove_class("visible")
        if self._cached_prompt is not None:
            self._cached_prompt.focus()
        self._set_nav("prompt")

    def _suggest_visible(self) -> bool:
        suggest = self._cached_slash_suggest
        if suggest is None:
            return False
        return "visible" in suggest.classes and suggest.option_count > 0

    def _set_nav(self, state: str) -> None:
        self._nav_state = state
        self._update_nav_hint()

    def action_tab_action(self) -> None:
        if self._nav_state == "suggest":
            suggest = self._cached_slash_suggest
            prompt = self._cached_prompt
            if suggest is not None and prompt is not None and suggest.option_count > 0:
                idx = suggest.highlighted or 0
                suggest.highlighted = (idx + 1) % suggest.option_count
                opt = suggest.get_option_at_index(suggest.highlighted)
                if opt and opt.id:
                    prompt.text = str(opt.id) + " "
            return
        self.screen.focus_next()

    def action_shift_tab_action(self) -> None:
        if self._nav_state == "suggest":
            suggest = self._cached_slash_suggest
            prompt = self._cached_prompt
            if suggest is not None and prompt is not None and suggest.option_count > 0:
                idx = suggest.highlighted or 0
                suggest.highlighted = (idx - 1) % suggest.option_count
                opt = suggest.get_option_at_index(suggest.highlighted)
                if opt and opt.id:
                    prompt.text = str(opt.id) + " "
            return
        self.screen.focus_previous()

    def action_escape_action(self) -> None:
        if self._nav_state == "suggest":
            self.action_hide_suggest()
        elif self._nav_state in {"hitl", "transcript"}:
            self._set_nav("prompt")
            (self._cached_prompt or self.query_one("#prompt", AutoGrowTextArea)).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "slash-suggest":
            return
        chosen = str(event.option.id or "")
        if not chosen:
            return
        prompt = self._cached_prompt
        if prompt is not None:
            prompt.text = chosen + " "
            prompt.focus()
        suggest = self._cached_slash_suggest
        if suggest is not None:
            suggest.remove_class("visible")

    # ── Actions ──────────────────────────────────────────────────────

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
        self.push_screen(ArtifactsScreen(self._client, self.thread_id), lambda _: None)

    def action_open_runs(self) -> None:
        self.push_screen(RunsScreen(self._client, self.thread_id), lambda _: None)

    def action_open_shortcuts(self) -> None:
        self.push_screen(ShortcutsScreen())

    # ── Properties ───────────────────────────────────────────────────

    @property
    def _transcript(self) -> Transcript:
        if self._cached_transcript is not None:
            return self._cached_transcript
        return self.query_one("#transcript", Transcript)

    @property
    def _status_bar(self) -> StatusBar:
        if self._cached_status_bar is not None:
            return self._cached_status_bar
        return self.query_one("#status-bar", StatusBar)

    # ── Nav hints ────────────────────────────────────────────────────

    def _update_nav_hint(self) -> None:
        hints = {
            "prompt": "",
            "suggest": "tab/shift+tab cycle · enter accept · esc close",
            "hitl": "tab/shift+tab navigate · enter submit · esc back",
            "transcript": "↑↓ scroll · tab prompt",
        }
        target = hints.get(self._nav_state, "")
        if target == self._last_nav_override:
            return
        self._last_nav_override = target
        sb = self._status_bar
        if target:
            sb.set_override(target)
        else:
            sb.clear_override()

    # ── Public hooks ─────────────────────────────────────────────────

    def prefill_input(self, text: str) -> None:
        prompt = self._cached_prompt or self.query_one("#prompt", AutoGrowTextArea)
        prompt.text = text
        prompt.focus()

    # ── Turn execution ───────────────────────────────────────────────

    async def _handle_user_text(self, text: str) -> None:
        transcript = self._transcript
        transcript.append(f"[user] {text}")

        cmd_ctx = self._make_command_context()
        if await dispatch_slash(cmd_ctx, text):
            return

        transcript.append("[info] running workflow...")
        transcript.defer_scroll()
        try:
            thread_id = await self._ensure_thread_id()
            self.busy = True
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
            transcript.flush_scroll()

    def _writer(self, line: str) -> None:
        if line.startswith(("│", "[")) and self.thread_id:
            from monet.cli.chat._view import _AGENT_TAG_RE

            if line.startswith("│") or _AGENT_TAG_RE.match(line):
                self._thread_progress.setdefault(self.thread_id, []).append(line)
        self._log_to_thread(line)
        markdown = line.startswith("[assistant]")
        scroll = not line.startswith("│")
        if scroll and markdown:
            self._transcript.append(line, markdown=markdown, scroll=False)
        else:
            self._transcript.append(line, markdown=markdown, scroll=scroll)

    def _log_to_thread(self, line: str) -> None:
        if not self.thread_id:
            return
        from pathlib import Path

        path = Path.cwd() / ".cli-logs" / "threads" / f"{self.thread_id}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy

    def _focus_prompt(self) -> None:
        (self._cached_prompt or self.query_one("#prompt", AutoGrowTextArea)).focus()
        self._set_nav("prompt")

    async def _load_thread_name(self) -> None:
        """Fetch and display the thread name for a resumed session."""
        try:
            name = await self._client.chat.get_chat_name(self.thread_id)
        except Exception:
            return
        if name:
            self.title = f"monet chat · {name}"
            self._status_bar.update_segments(thread_name=name)

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
        self._set_nav("hitl")
        return True

    def _unmount_hitl_widgets(self) -> None:
        self._hitl_envelope = None
        self._transcript.unmount_hitl()
        self._set_nav("prompt")

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
        for line in self._thread_progress.get(target, []):
            self._transcript.append(line)
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
        async def _get_agents() -> int:
            try:
                return len(await self._client.list_capabilities())
            except Exception:
                return 0

        async def _get_artifacts() -> int:
            if not self.thread_id:
                return 0
            try:
                return len(await self._client.list_artifacts(thread_id=self.thread_id))
            except Exception:
                return 0

        async def _get_runs() -> int:
            if not self.thread_id:
                return 0
            try:
                return await self._client.chat.count_thread_runs(self.thread_id)
            except Exception:
                return 0

        agents, artifacts, runs = await asyncio.gather(
            _get_agents(), _get_artifacts(), _get_runs()
        )
        self._status_bar.update_segments(agents=agents, artifacts=artifacts, runs=runs)

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
        screen_actions: dict[str, Callable[[], None]] = {
            "threads": self.action_open_threads,
            "agents": self.action_open_agents,
            "artifacts": self.action_open_artifacts,
            "runs": self.action_open_runs,
            "shortcuts": self.action_open_shortcuts,
        }
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
            copy_to_clipboard=self.copy_to_clipboard,
            exit_app=self.exit,
            push_screen=lambda name: screen_actions.get(name, lambda: None)(),
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
