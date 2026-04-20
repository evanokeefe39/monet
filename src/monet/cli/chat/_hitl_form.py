"""Inline HITL widgets for the monet chat TUI.

Two widget paths:

- :class:`InlinePicker` — compact numbered picker for envelopes matching
  :class:`~monet.cli.chat._protocols.InlinePickProtocol`.
- :class:`InlineForm` — generic field stack for other envelope shapes.

Widgets post :class:`~monet.cli.chat._messages.HitlSubmitted` on submit.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, ClassVar

from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import (
    Checkbox,
    Input,
    OptionList,
    RadioButton,
    RadioSet,
    Select,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from monet.cli.chat._protocols import InlinePickProtocol, InlinePickShape

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import ComposeResult
    from textual.widget import Widget

    from monet.types import EnvelopeField, InterruptEnvelope

SubmitCallback = "Callable[[dict[str, Any] | None], None]"


# Field types we render as widgets. Anything outside this set falls back
# to a plain Input (text-like) so unknown-type envelopes still work.
_WIDGET_TYPES = frozenset(
    {
        "text",
        "textarea",
        "int",
        "bool",
        "radio",
        "select",
        "checkbox",
        "hidden",
    }
)


def envelope_supports_widgets(envelope: InterruptEnvelope) -> bool:
    """True when every non-hidden field has a known widget mapping.

    Used by the app to decide whether to mount widgets or fall back to
    the transcript text-parse path. Returning False for any unknown
    type keeps the fallback predictable — users with exotic custom
    graphs get the typed-reply experience instead of a partial form.
    """
    if envelope is None:
        return False
    if not envelope.fields:
        return False
    return all(f.type in _WIDGET_TYPES for f in envelope.fields)


class InlinePicker(Vertical):
    """Compact numbered picker for :class:`InlinePickProtocol` envelopes.

    Renders like Claude Code's plan-mode picker: one option per line,
    optional free-text Input underneath. No border, no Submit button —
    selecting an option (Enter in the :class:`OptionList` or in the
    Input) submits the payload directly.

    Payload keys are taken from the envelope's own field names, so no
    planner vocabulary is baked in.
    """

    DEFAULT_CSS = """
    InlinePicker {
        height: auto;
        margin: 1 0;
        background: transparent;
        padding: 0 0;
    }
    InlinePicker .picker-prompt {
        text-style: bold;
        padding-bottom: 1;
    }
    InlinePicker OptionList {
        border: none;
        background: transparent;
        height: auto;
    }
    InlinePicker Input {
        border: none;
        background: transparent;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        envelope: InterruptEnvelope,
        shape: InlinePickShape,
        on_submit: Callable[[dict[str, Any] | None], None],
    ) -> None:
        super().__init__(id="hitl-picker")
        self._envelope = envelope
        self._shape = shape
        self._on_submit = on_submit
        # Carry-through values for hidden fields keyed by field name.
        self._hidden_values: dict[str, Any] = {}
        for f in envelope.fields:
            if f.type == "hidden" and f.name:
                self._hidden_values[f.name] = (
                    f.value if f.value is not None else f.default
                )

    def compose(self) -> ComposeResult:
        if self._envelope.prompt:
            yield Static(self._envelope.prompt, classes="picker-prompt")
        options: list[Option] = []
        for i, opt in enumerate(self._shape.radio.options, start=1):
            label = f"{i}. {opt.label or opt.value}"
            options.append(Option(label, id=opt.value))
        yield OptionList(*options, id="picker-list")
        if self._shape.text is not None:
            placeholder = self._shape.text.label or self._shape.text.name or ""
            default = str(self._shape.text.default) if self._shape.text.default else ""
            yield Input(
                value=default,
                placeholder=placeholder,
                id="picker-text",
            )

    def on_mount(self) -> None:
        # Focus the option list so arrow keys / Enter are immediately
        # live without the user having to tab from the prompt.
        with contextlib.suppress(Exception):
            self.query_one("#picker-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "picker-list":
            return
        event.stop()
        self._submit(option_id=str(event.option.id or ""))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "picker-text":
            return
        event.stop()
        # Submit with whichever option is currently highlighted — if the
        # user never moved the cursor, that's option 0.
        picker = self.query_one("#picker-list", OptionList)
        idx = picker.highlighted if picker.highlighted is not None else 0
        options = self._shape.radio.options
        if 0 <= idx < len(options):
            self._submit(option_id=options[idx].value)

    def _submit(self, *, option_id: str) -> None:
        payload: dict[str, Any] = dict(self._hidden_values)
        payload[self._shape.radio.name] = option_id
        if self._shape.text is not None:
            text_widget = self.query_one("#picker-text", Input)
            payload[self._shape.text.name] = text_widget.value
        self._on_submit(payload)


class InlineForm(Vertical):
    """Lightweight inline form for envelopes that don't match InlinePickProtocol.

    No border, no submit button — Enter submits. Renders fields
    vertically with minimal chrome.
    """

    DEFAULT_CSS = """
    InlineForm {
        height: auto;
        margin: 1 0;
        background: transparent;
        padding: 0 1;
    }
    InlineForm .iform-prompt {
        text-style: bold;
        padding-bottom: 1;
    }
    InlineForm .iform-label {
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS: ClassVar = [
        Binding("enter", "submit_form", "Submit", show=False, priority=True),
    ]

    def __init__(
        self,
        envelope: InterruptEnvelope,
        on_submit: Callable[[dict[str, Any] | None], None],
    ) -> None:
        super().__init__(id="hitl-form")
        self._envelope = envelope
        self._on_submit = on_submit
        self._widgets: dict[str, Any] = {}
        self._hidden_values: dict[str, Any] = {}
        self._radio_options: dict[str, list[str]] = {}
        self._checkbox_groups: dict[str, list[tuple[Checkbox, str]]] = {}
        self._build()

    def _build(self) -> None:
        for field in self._envelope.fields:
            name = field.name
            if not name:
                continue
            if field.type == "hidden":
                self._hidden_values[name] = (
                    field.value if field.value is not None else field.default
                )
                continue
            widget = self._build_field(field)
            if widget is not None:
                self._widgets[name] = widget

    def _build_field(self, field: EnvelopeField) -> Any:
        label = field.label or field.name
        if field.type == "textarea":
            default = "" if field.default is None else str(field.default)
            return TextArea(text=default, id=f"hitl-f-{field.name}")
        if field.type == "int":
            default = "" if field.default is None else str(field.default)
            return Input(value=default, type="integer", id=f"hitl-f-{field.name}")
        if field.type == "bool":
            return Checkbox(label, value=bool(field.default), id=f"hitl-f-{field.name}")
        if field.type == "radio":
            values = [o.value for o in field.options]
            self._radio_options[field.name] = values
            buttons = []
            for i, opt in enumerate(field.options):
                pressed = (
                    opt.value == field.default if field.default is not None else i == 0
                )
                buttons.append(RadioButton(opt.label or opt.value, value=pressed))
            return RadioSet(*buttons, id=f"hitl-f-{field.name}")
        if field.type == "select":
            options = [(o.label or o.value, o.value) for o in field.options]
            initial: Any = field.default if field.default is not None else Select.BLANK
            return Select(options, value=initial, id=f"hitl-f-{field.name}")
        if field.type == "checkbox":
            defaults = field.default if isinstance(field.default, list) else []
            group: list[tuple[Checkbox, str]] = []
            for i, opt in enumerate(field.options):
                cb = Checkbox(
                    opt.label or opt.value,
                    value=opt.value in defaults,
                    id=f"hitl-f-{field.name}-{i}",
                )
                group.append((cb, opt.value))
            self._checkbox_groups[field.name] = group
            return group
        default = "" if field.default is None else str(field.default)
        return Input(value=default, id=f"hitl-f-{field.name}")

    def compose(self) -> ComposeResult:
        if self._envelope.prompt:
            yield Static(self._envelope.prompt, classes="iform-prompt")
        for field in self._envelope.fields:
            if field.type == "hidden" or not field.name:
                continue
            label_text = field.label or field.name
            if field.type != "bool":
                yield Static(label_text, classes="iform-label")
            widget = self._widgets[field.name]
            if field.type == "checkbox":
                for cb, _val in self._checkbox_groups[field.name]:
                    yield cb
            else:
                yield widget

    def action_submit_form(self) -> None:
        self._submit()

    def _submit(self) -> None:
        payload = _collect_inline_values(self, self._envelope)
        self._on_submit(payload)


def _collect_inline_values(
    form: InlineForm,
    envelope: InterruptEnvelope,
) -> dict[str, Any] | None:
    """Harvest InlineForm widget state into a resume payload."""
    payload: dict[str, Any] = dict(form._hidden_values)
    for field in envelope.fields:
        if field.type == "hidden" or not field.name:
            continue
        widget = form._widgets.get(field.name)
        if widget is None:
            continue
        value = _read_inline_field(form, field, widget)
        if value is None and field.required:
            return None
        payload[field.name] = value
    return payload


def _read_inline_field(form: InlineForm, field: EnvelopeField, widget: Any) -> Any:
    if field.type == "textarea":
        return widget.text
    if field.type == "int":
        raw = widget.value.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None
    if field.type == "bool":
        return bool(widget.value)
    if field.type == "radio":
        values = form._radio_options.get(field.name) or []
        idx = getattr(widget, "pressed_index", -1)
        if idx is None or idx < 0 or idx >= len(values):
            return None
        return values[idx]
    if field.type == "select":
        val = widget.value
        if val is Select.BLANK:
            return None
        return val
    if field.type == "checkbox":
        group = form._checkbox_groups.get(field.name) or []
        return [value for cb, value in group if cb.value]
    raw = widget.value
    if raw == "" and field.required:
        return None
    return raw


def build_hitl_widget(
    envelope: InterruptEnvelope,
    on_submit: Callable[[dict[str, Any] | None], None],
) -> Widget:
    """Pick the widget for *envelope* per the TUI's rendering protocols.

    Protocols live in :mod:`monet.cli.chat._protocols`. The factory is
    the single dispatch point — adding a new protocol is a new branch
    here plus a new widget class; nothing else in the TUI changes.
    """
    if InlinePickProtocol.matches(envelope):
        return InlinePicker(envelope, InlinePickProtocol.extract(envelope), on_submit)
    return InlineForm(envelope, on_submit)


def build_submit_summary(
    envelope: InterruptEnvelope,
    payload: dict[str, Any],
) -> str:
    """One-line transcript summary of what the user submitted.

    Hides empty / blank feedback so ``action=approve`` doesn't read as
    ``action=approve, feedback=''``. Used only for the ``[user]`` line;
    the real payload goes to ``resume`` verbatim.
    """
    parts: list[str] = []
    for field in envelope.fields:
        name = field.name
        if not name or field.type == "hidden":
            continue
        value = payload.get(name)
        if value in (None, "", []):
            continue
        parts.append(f"{name}={value}")
    return ", ".join(parts) or "(submitted)"


__all__ = [
    "InlineForm",
    "InlinePicker",
    "build_hitl_widget",
    "build_submit_summary",
    "envelope_supports_widgets",
]
