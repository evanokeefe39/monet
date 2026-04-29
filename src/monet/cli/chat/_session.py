"""SessionController — invisible Widget owning all chat business logic.

The App shell (ChatApp) delegates every stateful operation here.
SessionController never imports ChatApp at runtime; it accesses the host
App via ``self.app`` with ``# type: ignore`` where ChatApp-specific
attributes are needed (``busy``, ``thread_id``, ``title``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from textual.widget import Widget

from monet.cli.chat._constants import (
    INDICATOR_REFRESH_SECONDS,
    SLASH_SUGGEST_DEBOUNCE,
    TUI_COMMANDS,
)
from monet.cli.chat._hitl._coordinator import InterruptCoordinator
from monet.cli.chat._hitl._widgets import build_hitl_widget
from monet.cli.chat._slash._router import CommandContext, dispatch_slash
from monet.cli.chat._slash._suggester import RegistrySuggester

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.timer import Timer
    from textual.worker import Worker

    from monet.cli.chat._prompt import AutoGrowTextArea
    from monet.cli.chat._slash._overlay import SlashOverlay
    from monet.cli.chat._status_bar import StatusBar
    from monet.cli.chat._transcript import Transcript
    from monet.client import MonetClient

_log = logging.getLogger("monet.cli.chat")
_MAX_PROGRESS_LINES = 500


class SessionController(Widget):
    """Invisible widget owning chat session state and business logic.

    Precondition: ``setup()`` is called from the host App's ``on_mount``
    after all sibling widgets are in the DOM.
    """

    DEFAULT_CSS = "SessionController { height: 0; display: none; }"

    def __init__(
        self,
        *,
        client: MonetClient,
        initial_thread_id: str,
        server_slash_commands: list[str],
        initial_transcript: list[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._initial_thread_id = initial_thread_id
        self._initial_transcript = initial_transcript
        self._server_slash_commands: list[str] = list(server_slash_commands)
        self._interrupts = InterruptCoordinator()
        self._turn_worker: Worker[None] | None = None
        self._thread_progress: dict[str, list[str]] = {}
        self._hitl_envelope: Any = None
        self._nav_state: str = "prompt"
        self._last_nav_override: str = ""
        self._welcome_shown = False
        self._slash_timer: Timer | None = None

        # slash suggest UI state — shared with App
        self.slash_commands: list[str] = self._build_slash_commands()
        self.slash_descriptions: dict[str, str] = dict(TUI_COMMANDS)
        self._suggester = RegistrySuggester(self.slash_commands)

        # widget refs — set by setup()
        self._transcript_w: Transcript | None = None
        self._status_bar_w: StatusBar | None = None
        self._prompt_w: AutoGrowTextArea | None = None
        self._slash_suggest_w: SlashOverlay | None = None

    # ── Setup ────────────────────────────────────────────────────────

    def setup(
        self,
        *,
        transcript: Transcript,
        status_bar: StatusBar,
        prompt: Any,
        slash_suggest: SlashOverlay | None,
    ) -> None:
        """Wire widget refs from the host App. Called once from App.on_mount."""
        self._transcript_w = transcript
        self._status_bar_w = status_bar
        self._prompt_w = prompt
        self._slash_suggest_w = slash_suggest

    def on_mount(self) -> None:
        self.set_interval(INDICATOR_REFRESH_SECONDS, self._refresh_indicator)
        if self._initial_thread_id:
            self.run_worker(self._load_thread_name(), exclusive=False)
            self.run_worker(self._recover_pending_interrupt(), exclusive=False)
        self.run_worker(self._refresh_slash_commands(), exclusive=False)

    # ── Internal widget accessors ────────────────────────────────────

    @property
    def _t(self) -> Transcript:
        from monet.cli.chat._transcript import Transcript

        if self._transcript_w is not None:
            return self._transcript_w
        return self.app.query_one("#transcript", Transcript)  # type: ignore[return-value]

    @property
    def _sb(self) -> StatusBar:
        from monet.cli.chat._status_bar import StatusBar

        if self._status_bar_w is not None:
            return self._status_bar_w
        return self.app.query_one("#status-bar", StatusBar)  # type: ignore[return-value]

    @property
    def _prompt(self) -> AutoGrowTextArea | None:
        return self._prompt_w

    @property
    def _suggest(self) -> SlashOverlay | None:
        return self._slash_suggest_w

    # ── Slash suggest state ──────────────────────────────────────────

    def _build_slash_commands(self) -> list[str]:
        out: list[str] = [cmd for cmd, _ in TUI_COMMANDS]
        seen = set(out)
        for cmd in self._server_slash_commands:
            if cmd not in seen:
                out.append(cmd)
                seen.add(cmd)
        return out

    # ── Prompt handling ──────────────────────────────────────────────

    def handle_prompt_submitted(self, event: Any) -> None:
        """Dispatch a user prompt: slash command or chat turn."""
        if self._welcome_shown:
            return
        suggest = self._suggest
        prompt = self._prompt
        if self._suggest_visible() and suggest is not None:
            cmd = suggest.accept_highlighted()
            if cmd and prompt is not None:
                prompt.text = cmd + " "
            suggest.hide()
            self._set_nav("prompt")
            return
        text = event.text.strip()
        if not text:
            return
        if self._interrupts.is_pending():
            self._interrupts.consume_if_pending(text)
            return
        if self.app.busy:  # type: ignore[attr-defined]
            return
        self._turn_worker = self.run_worker(
            self._handle_user_text(text), exclusive=False
        )

    def handle_hitl_submitted(self, msg: Any) -> None:
        envelope = self._hitl_envelope
        if envelope is None:
            return
        if msg.payload is None:
            self.app.notify("please fill in every required field", severity="warning")
            return
        self._interrupts.consume_payload(msg.payload)

    def handle_hitl_dismissed(self, msg: Any) -> None:
        self._t.unmount_hitl()
        self._t.append("[info] interrupt dismissed")

    # ── Run control ──────────────────────────────────────────────────

    def cancel_run(self) -> None:
        if self._turn_worker is not None:
            self._turn_worker.cancel()
            self._turn_worker = None
        self.app.busy = False  # type: ignore[attr-defined]
        self._t.append("[info] run cancelled")

    # ── Welcome ──────────────────────────────────────────────────────

    def show_welcome(self) -> None:
        self._welcome_shown = True
        try:
            self._t.show_welcome()
            self._sb.display = False
            self.app.query_one("#prompt-area").display = False  # type: ignore[union-attr]
        except Exception:
            _log.debug("show welcome failed", exc_info=True)

    def dismiss_welcome(self) -> None:
        self._welcome_shown = False
        self._sb.display = True
        self.app.query_one("#prompt-area").display = True  # type: ignore[union-attr]
        if self._prompt is not None:
            self._prompt.focus()

    # ── Turn execution ───────────────────────────────────────────────

    async def _handle_user_text(self, text: str) -> None:
        transcript = self._t
        transcript.append(f"[user] {text}")

        cmd_ctx = self._make_command_context()
        if await dispatch_slash(cmd_ctx, text):
            return

        transcript.append("[info] running workflow...")
        transcript.defer_scroll()
        try:
            thread_id = await self._ensure_thread_id()
            self.app.busy = True  # type: ignore[attr-defined]
            from monet.cli.chat._turn import run_turn

            stream = self._client.chat.send_message(thread_id, text)
            await run_turn(
                client=self._client,
                thread_id=thread_id,
                first_stream=stream,
                coordinator=self._interrupts,  # type: ignore[arg-type]
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
            self.app.busy = False  # type: ignore[attr-defined]
            self._turn_worker = None
            transcript.flush_scroll()

    def _writer(self, line: str) -> None:
        thread_id: str = self.app.thread_id  # type: ignore[attr-defined]
        if line.startswith(("│", "[")) and thread_id:
            from monet.cli.chat._view import _AGENT_TAG_RE

            if line.startswith("│") or _AGENT_TAG_RE.match(line):
                lines = self._thread_progress.setdefault(thread_id, [])
                lines.append(line)
                if len(lines) > _MAX_PROGRESS_LINES:
                    self._thread_progress[thread_id] = lines[-_MAX_PROGRESS_LINES:]
        self._log_to_thread(line)
        markdown = line.startswith("[assistant]")
        scroll = not line.startswith("│")
        if scroll and markdown:
            self._t.append(line, markdown=markdown, scroll=False)
        else:
            self._t.append(line, markdown=markdown, scroll=scroll)

    def _log_to_thread(self, line: str) -> None:
        thread_id: str = self.app.thread_id  # type: ignore[attr-defined]
        if not thread_id:
            return
        from pathlib import Path

        path = Path.cwd() / ".cli-logs" / "threads" / f"{thread_id}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _set_busy(self, busy: bool) -> None:
        self.app.busy = busy  # type: ignore[attr-defined]

    def _focus_prompt(self) -> None:
        from monet.cli.chat._prompt import AutoGrowTextArea

        (self._prompt or self.app.query_one("#prompt", AutoGrowTextArea)).focus()
        self._set_nav("prompt")

    # ── Thread management ────────────────────────────────────────────

    async def _ensure_thread_id(self) -> str:
        current: str = self.app.thread_id  # type: ignore[attr-defined]
        if current:
            return current
        from monet.cli._namegen import random_chat_name

        name = random_chat_name()
        new_id = await self._client.chat.create_chat(name=name)
        self.app.thread_id = new_id  # type: ignore[attr-defined]
        self.app.title = f"monet chat · {name}"  # type: ignore[attr-defined]
        self._sb.update_segments(thread_name=name)
        return new_id

    async def _load_thread_name(self) -> None:
        try:
            name = await self._client.chat.get_chat_name(
                self.app.thread_id  # type: ignore[attr-defined]
            )
        except Exception:
            return
        if name:
            self.app.title = f"monet chat · {name}"  # type: ignore[attr-defined]
            self._sb.update_segments(thread_name=name)

    async def switch_thread(self, target: str) -> None:
        if self._turn_worker is not None:
            self._turn_worker.cancel()
            self._turn_worker = None
        self._t.clear()
        self._thread_progress.clear()
        self.app.thread_id = target  # type: ignore[attr-defined]
        self.app.title = f"monet chat · {target}"  # type: ignore[attr-defined]
        self._sb.update_segments(thread_name="")
        self._t.append(f"[info] switched to {target}")
        try:
            entries = await self._client.chat.get_thread_transcript(target)
            self._render_transcript_stream(entries)
        except Exception:
            _log.debug("transcript fetch failed for %s", target, exc_info=True)
            try:
                history = await self._client.chat.get_chat_history(target)
                self._t.load_history(history)
            except Exception:
                _log.debug("history fallback failed for %s", target, exc_info=True)
        self.run_worker(self._load_thread_name(), exclusive=False)
        self.run_worker(self._recover_pending_interrupt(), exclusive=False)

    # ── HITL widgets ─────────────────────────────────────────────────

    def _mount_hitl_widgets(self, form: dict[str, Any]) -> bool:
        from monet.cli.chat._hitl._widgets import envelope_supports_widgets
        from monet.types import InterruptEnvelope

        envelope = InterruptEnvelope.from_interrupt_values(form)
        if envelope is None or not envelope_supports_widgets(envelope):
            return False
        try:
            widget = build_hitl_widget(envelope, self._on_hitl_payload)
            self._t.mount_hitl(widget)
        except Exception:
            _log.exception("mount hitl widgets failed")
            return False
        self._hitl_envelope = envelope
        self._set_nav("hitl")
        return True

    def _unmount_hitl_widgets(self) -> None:
        self._hitl_envelope = None
        self._t.unmount_hitl()
        self._set_nav("prompt")

    def _on_hitl_payload(self, payload: dict[str, Any] | None) -> None:
        if payload is None:
            self.app.notify("please fill in every required field", severity="warning")
            return
        self._interrupts.consume_payload(payload)

    # ── Interrupt recovery ───────────────────────────────────────────

    async def _recover_pending_interrupt(self) -> None:
        thread_id: str = self.app.thread_id  # type: ignore[attr-defined]
        if not thread_id:
            return
        try:
            pending = await self._client.chat.get_chat_interrupt(thread_id)
        except Exception:
            return
        if not pending:
            return
        self._t.append("[info] resuming interrupted run...")
        self._mount_hitl_widgets(pending)

    # ── Transcript rendering ─────────────────────────────────────────

    def render_initial_history(self) -> None:
        self._render_transcript_stream(self._initial_transcript)

    def _render_transcript_stream(self, entries: list[dict[str, Any]]) -> None:
        from monet.cli.chat._view import (
            format_agent_header,
            format_progress_line,
        )

        seen_agents: set[str] = set()
        current_run_id: str = ""
        last_progress_line: str = ""

        for entry in entries:
            etype = entry.get("type")
            data = entry.get("data") or {}

            if etype == "message":
                try:
                    from langchain_core.messages import (
                        AIMessage,
                        HumanMessage,
                        convert_to_messages,
                    )

                    _type_to_role: dict[str, str] = {
                        "ai": "assistant",
                        "human": "user",
                        "system": "system",
                    }
                    [msg] = convert_to_messages([data])
                    if isinstance(msg, AIMessage):
                        role = "assistant"
                    elif isinstance(msg, HumanMessage):
                        role = "user"
                    else:
                        role = str(
                            data.get("role")
                            or _type_to_role.get(str(data.get("type") or ""), "user")
                        )
                    content = str(
                        getattr(msg, "content", None) or data.get("content") or ""
                    )
                except Exception:
                    role = str(data.get("role") or "user")
                    content = str(data.get("content") or "")
                self._t.append(f"[{role}] {content}", markdown=(role == "assistant"))

            elif etype == "telemetry":
                agent = data.get("agent_id") or data.get("agent")
                if not agent:
                    continue
                cmd = data.get("command") or ""
                status = data.get("status") or ""
                reasons = data.get("reasons") or ""
                run_id = data.get("run_id") or ""
                # Reset per-run state when crossing a run boundary so agent
                # headers render correctly on second and subsequent runs.
                if run_id and run_id != current_run_id:
                    seen_agents.clear()
                    current_run_id = run_id
                agent_key = f"{agent}:{cmd}" if cmd else agent
                if agent_key not in seen_agents:
                    seen_agents.add(agent_key)
                    self._t.append(format_agent_header(agent_key))
                from monet.client._events import AgentProgress

                p = AgentProgress(
                    run_id=str(run_id),
                    agent_id=str(agent),
                    status=str(status),
                    command=str(cmd),
                    reasons=str(reasons),
                )
                pline = format_progress_line(p)
                if pline and pline != last_progress_line:
                    self._t.append(pline)
                    last_progress_line = pline

    def copy_artifact_url(self, artifact_id: str) -> None:
        base = getattr(self._client, "_url", "") or ""
        url = f"{base.rstrip('/')}/api/v1/artifacts/{artifact_id}/view"
        try:
            self.app.copy_to_clipboard(url)
            self.app.notify(f"copied artifact url · {artifact_id[:8]}")
        except Exception as exc:
            _log.warning("copy failed: %s", exc)

    # ── Command context ──────────────────────────────────────────────

    def _make_command_context(self) -> CommandContext:
        return CommandContext(
            client=self._client,
            transcript=self._t,
            thread_id=self.app.thread_id,  # type: ignore[attr-defined]
            server_slash_commands=self._server_slash_commands,
            app=self.app,  # type: ignore[arg-type]
        )

    # ── Screen actions ───────────────────────────────────────────────

    def open_threads(self, current_thread_id: str) -> None:
        from monet.cli.chat._screens import ThreadsScreen

        def _on_pick(result: str | None) -> None:
            if result:
                self.run_worker(self.switch_thread(result), exclusive=True)

        self.app.push_screen(ThreadsScreen(self._client, current_thread_id), _on_pick)

    def open_agents(self) -> None:
        from monet.cli.chat._screens import AgentsScreen

        def _on_pick(result: str | None) -> None:
            if result:
                self.prefill_input(result + " ")

        self.app.push_screen(AgentsScreen(self._client), _on_pick)

    def open_artifacts(self, thread_id: str) -> None:
        from monet.cli.chat._screens import ArtifactsScreen

        if not thread_id:
            self._t.append("[info] no active thread for artifacts")
            return
        self.app.push_screen(ArtifactsScreen(self._client, thread_id), lambda _: None)

    def open_runs(self, thread_id: str) -> None:
        from monet.cli.chat._screens import RunsScreen

        self.app.push_screen(RunsScreen(self._client, thread_id), lambda _: None)

    def prefill_input(self, text: str) -> None:
        from monet.cli.chat._prompt import AutoGrowTextArea

        prompt = self._prompt or self.app.query_one("#prompt", AutoGrowTextArea)
        prompt.text = text
        prompt.focus()

    def push_screen_by_name(self, name: str) -> None:
        thread_id: str = self.app.thread_id  # type: ignore[attr-defined]
        actions: dict[str, Callable[[], None]] = {
            "threads": lambda: self.open_threads(thread_id),
            "agents": self.open_agents,
            "artifacts": lambda: self.open_artifacts(thread_id),
            "runs": lambda: self.open_runs(thread_id),
            "shortcuts": lambda: self.app.push_screen(  # type: ignore[dict-item]
                __import__(
                    "monet.cli.chat._screens",
                    fromlist=["ShortcutsScreen"],
                ).ShortcutsScreen()
            ),
        }
        action = actions.get(name)
        if action is not None:
            action()

    # ── Slash suggest ────────────────────────────────────────────────

    def on_text_area_changed(self, event: Any) -> None:
        if getattr(event, "text_area", None) is None or event.text_area.id != "prompt":
            return
        suggest = self._suggest
        stripped = event.text_area.text.strip()
        if not stripped.startswith("/") or " " in stripped:
            if self._slash_timer is not None:
                self._slash_timer.stop()
                self._slash_timer = None
            if suggest is not None:
                suggest.hide()
            self._update_nav_hint()
            return
        if self._slash_timer is not None:
            self._slash_timer.stop()
        self._slash_timer = self.set_timer(
            SLASH_SUGGEST_DEBOUNCE, self._do_slash_suggest
        )

    def _do_slash_suggest(self) -> None:
        self._slash_timer = None
        suggest = self._suggest
        prompt = self._prompt
        if suggest is None or prompt is None:
            return
        became_visible = suggest.refresh_suggest(
            prompt.text, self.slash_commands, self.slash_descriptions
        )
        if became_visible:
            self._set_nav("suggest")
        elif self._nav_state == "suggest":
            self._set_nav("prompt")

    def on_slash_overlay_accepted(self, msg: SlashOverlay.Accepted) -> None:
        prompt = self._prompt
        if prompt is not None:
            prompt.text = msg.command + " "
            prompt.focus()
        suggest = self._suggest
        if suggest is not None:
            suggest.hide()
        self._set_nav("prompt")

    def _suggest_visible(self) -> bool:
        suggest = self._suggest
        return suggest is not None and suggest.is_active

    # ── Nav / keyboard actions ───────────────────────────────────────

    def _set_nav(self, state: str) -> None:
        self._nav_state = state
        self._update_nav_hint()

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
        if target:
            self._sb.set_override(target)
        else:
            self._sb.clear_override()

    def action_tab(self) -> None:
        if self._nav_state == "suggest":
            suggest = self._suggest
            prompt = self._prompt
            if suggest is not None and prompt is not None:
                cmd = suggest.cycle(1)
                if cmd:
                    prompt.text = cmd + " "
            return
        self.app.screen.focus_next()

    def action_shift_tab(self) -> None:
        if self._nav_state == "suggest":
            suggest = self._suggest
            prompt = self._prompt
            if suggest is not None and prompt is not None:
                cmd = suggest.cycle(-1)
                if cmd:
                    prompt.text = cmd + " "
            return
        self.app.screen.focus_previous()

    def action_escape(self) -> None:
        from monet.cli.chat._prompt import AutoGrowTextArea

        if self._nav_state == "suggest":
            suggest = self._suggest
            if suggest is not None:
                suggest.hide()
            if self._prompt is not None:
                self._prompt.focus()
            self._set_nav("prompt")
        elif self._nav_state in {"hitl", "transcript"}:
            self._set_nav("prompt")
            (self._prompt or self.app.query_one("#prompt", AutoGrowTextArea)).focus()

    # ── Indicator + slash refresh ────────────────────────────────────

    async def _refresh_slash_commands(self) -> None:
        try:
            commands = await self._client.slash_commands()
        except Exception:
            return
        self._server_slash_commands = commands
        self.slash_commands = self._build_slash_commands()
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
        thread_id: str = self.app.thread_id  # type: ignore[attr-defined]

        async def _get_agents() -> int:
            try:
                return len(await self._client.list_capabilities())
            except Exception:
                return 0

        async def _get_artifacts() -> int:
            if not thread_id:
                return 0
            try:
                return len(await self._client.list_artifacts(thread_id=thread_id))
            except Exception:
                return 0

        async def _get_runs() -> int:
            if not thread_id:
                return 0
            try:
                return await self._client.chat.count_thread_runs(thread_id)
            except Exception:
                return 0

        agents, artifacts, runs = await asyncio.gather(
            _get_agents(), _get_artifacts(), _get_runs()
        )
        self._sb.update_segments(agents=agents, artifacts=artifacts, runs=runs)

    # ── Test helpers ─────────────────────────────────────────────────

    async def collect_resume(self, form: dict[str, Any]) -> dict[str, Any] | None:
        """Collect a HITL resume payload. Used by integration tests."""
        return await self._interrupts.collect(
            form,
            writer=self._writer,
            busy_setter=self._set_busy,
            focus_prompt=self._focus_prompt,
            mount_widgets=self._mount_hitl_widgets,
            unmount_widgets=self._unmount_hitl_widgets,
        )
