"""Structural tests for chat-TUI rendering protocols."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("textual")

from monet.cli.chat._protocols import InlinePickProtocol
from monet.types import InterruptEnvelope


def _env(fields: list[dict[str, Any]], prompt: str = "pick one") -> InterruptEnvelope:
    env = InterruptEnvelope.from_interrupt_values({"prompt": prompt, "fields": fields})
    assert env is not None
    return env


def _radio(name: str, n: int) -> dict[str, Any]:
    return {
        "name": name,
        "type": "radio",
        "options": [{"value": f"v{i}", "label": f"V{i}"} for i in range(n)],
    }


def test_inline_pick_matches_radio_plus_textarea() -> None:
    env = _env([_radio("decision", 3), {"name": "note", "type": "textarea"}])
    assert InlinePickProtocol.matches(env) is True


def test_inline_pick_matches_radio_only() -> None:
    env = _env([_radio("decision", 2)])
    assert InlinePickProtocol.matches(env) is True


def test_inline_pick_matches_radio_with_hidden() -> None:
    env = _env(
        [
            _radio("decision", 3),
            {"name": "run_id", "type": "hidden", "default": "abc"},
            {"name": "note", "type": "text"},
        ]
    )
    assert InlinePickProtocol.matches(env) is True


def test_inline_pick_rejects_radio_plus_int() -> None:
    env = _env([_radio("decision", 3), {"name": "n", "type": "int"}])
    assert InlinePickProtocol.matches(env) is False


def test_inline_pick_rejects_no_radio() -> None:
    env = _env([{"name": "note", "type": "text"}])
    assert InlinePickProtocol.matches(env) is False


def test_inline_pick_rejects_two_radios() -> None:
    env = _env([_radio("a", 2), _radio("b", 2)])
    assert InlinePickProtocol.matches(env) is False


def test_inline_pick_rejects_too_few_options() -> None:
    env = _env([_radio("decision", 1)])
    assert InlinePickProtocol.matches(env) is False


def test_inline_pick_rejects_too_many_options() -> None:
    env = _env([_radio("decision", 7)])
    assert InlinePickProtocol.matches(env) is False


def test_inline_pick_rejects_two_text_fields() -> None:
    env = _env(
        [
            _radio("decision", 3),
            {"name": "note", "type": "text"},
            {"name": "other", "type": "textarea"},
        ]
    )
    assert InlinePickProtocol.matches(env) is False


def test_inline_pick_extract_returns_field_refs() -> None:
    radio_spec = _radio("decision", 3)
    text_spec = {"name": "note", "type": "textarea"}
    env = _env([radio_spec, text_spec])
    shape = InlinePickProtocol.extract(env)
    # By object identity against the envelope's parsed fields.
    assert shape.radio is env.fields[0]
    assert shape.text is env.fields[1]


def test_inline_pick_extract_no_text_field() -> None:
    env = _env([_radio("decision", 2)])
    shape = InlinePickProtocol.extract(env)
    assert shape.radio is env.fields[0]
    assert shape.text is None
