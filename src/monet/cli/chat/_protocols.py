"""Rendering protocols for the monet chat TUI.

The chat TUI is one opinionated client implementation. The server emits
generic :class:`~monet.types.InterruptEnvelope` payloads; each client
(this TUI, a web UI, a third-party API consumer) decides independently
how to render them.

This module declares the TUI's *render contract* as a set of **rendering
protocols** — structural predicates over envelope shape that, when they
match, opt an envelope into a specific widget path. Envelopes that don't
match any protocol fall through to the generic form widget.

Protocols are:

- **Structural**: they inspect ``field.type`` and field counts only; they
  never look at ``field.name`` or ``option.value`` strings. That keeps
  the TUI decoupled from the default planner's vocabulary.
- **Published**: graph authors who want a given UX conform their
  interrupt envelopes to a protocol. The protocols are part of the
  client's public contract — alternate clients are free to declare
  their own.
- **Owned by the client**, not by ``monet.types``. The types layer
  defines the wire schema; this module declares what *this particular
  TUI* chooses to do with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.types import EnvelopeField, InterruptEnvelope


_INLINE_PICK_MAX_OPTIONS = 6
_INLINE_PICK_MIN_OPTIONS = 2
_FREE_TEXT_TYPES = frozenset({"text", "textarea"})


@dataclass(frozen=True)
class InlinePickShape:
    """Extracted references for an inline-pick envelope.

    ``radio`` is the one radio field; ``text`` is the free-text field
    (``type`` in ``{"text", "textarea"}``) when present, else ``None``.
    """

    radio: EnvelopeField
    text: EnvelopeField | None


class InlinePickProtocol:
    """Render contract for compact inline picker UX.

    Shape (structural only — no field-name or option-value matching):

    - exactly one ``radio`` field with 2-6 options,
    - zero or one free-text field (``text`` or ``textarea``),
    - any number of ``hidden`` fields.

    Any graph emitting this shape gets the picker. The payload submitted
    on selection is keyed by the envelope's own field names — no planner
    vocab baked into the TUI.
    """

    @staticmethod
    def matches(envelope: InterruptEnvelope) -> bool:
        radios: list[EnvelopeField] = []
        free_text: list[EnvelopeField] = []
        for f in envelope.fields:
            if f.type == "radio":
                radios.append(f)
            elif f.type in _FREE_TEXT_TYPES:
                free_text.append(f)
            elif f.type == "hidden":
                continue
            else:
                # Any other visible type disqualifies — use the generic form.
                return False
        if len(radios) != 1:
            return False
        if len(free_text) > 1:
            return False
        options = radios[0].options
        return _INLINE_PICK_MIN_OPTIONS <= len(options) <= _INLINE_PICK_MAX_OPTIONS

    @staticmethod
    def extract(envelope: InterruptEnvelope) -> InlinePickShape:
        """Pull the radio field and optional free-text field.

        Must only be called after :meth:`matches` returned True.
        """
        radio: EnvelopeField | None = None
        text: EnvelopeField | None = None
        for f in envelope.fields:
            if f.type == "radio" and radio is None:
                radio = f
            elif f.type in _FREE_TEXT_TYPES and text is None:
                text = f
        assert radio is not None, "extract() called on non-matching envelope"
        return InlinePickShape(radio=radio, text=text)


__all__ = ["InlinePickProtocol", "InlinePickShape"]
