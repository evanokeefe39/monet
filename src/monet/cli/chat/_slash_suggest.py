"""Slash-command autocomplete dropdown mixin for the chat TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual.widgets.option_list import Option

from monet.cli.chat._constants import SLASH_SUGGEST_DEBOUNCE, SLASH_SUGGEST_MAX_OPTIONS

if TYPE_CHECKING:
    from textual.timer import Timer
    from textual.widgets import OptionList

    from monet.cli.chat._prompt import AutoGrowTextArea


class SlashSuggestMixin:
    """Mixin providing slash-command autocomplete dropdown behaviour.

    Host must supply: ``_cached_slash_suggest``, ``_cached_prompt``,
    ``slash_commands``, ``slash_descriptions``, ``_slash_timer``,
    ``_last_slash_prefix``, ``_nav_state``, ``_update_nav_hint``,
    ``set_timer``, ``screen``.
    """

    _cached_slash_suggest: OptionList | None
    _cached_prompt: AutoGrowTextArea | None
    slash_commands: list[str]
    slash_descriptions: dict[str, str]
    _slash_timer: Timer | None
    _last_slash_prefix: str
    _nav_state: str

    # Provided by Textual App — declared here for type checkers.
    set_timer: Any
    screen: Any
    query_one: Any

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

    def _update_nav_hint(self) -> None:
        raise NotImplementedError

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
            from monet.cli.chat._prompt import AutoGrowTextArea

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
