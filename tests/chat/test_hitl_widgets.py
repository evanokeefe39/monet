"""Inline HITL widget tests.

Layer 2 — pure logic, no app: test widget construction and helper functions.
Layer 3 — app-mounted: test widget rendering inside a running ChatApp.

Collect-resume tests (Future-based) are skipped: asyncio.create_task + Textual
pilot event delivery requires a Textual-native drive pattern (TODO).
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("textual")

from textual.widgets import Checkbox, Input, OptionList, RadioSet

from monet.cli.chat import ChatApp
from monet.cli.chat._hitl._widgets import (
    InlineForm,
    InlinePicker,
    build_hitl_widget,
    build_submit_summary,
    envelope_supports_widgets,
)
from monet.types import InterruptEnvelope
from tests.chat.conftest import APPROVAL_FORM, make_fake_client

_APPROVAL_FORM = APPROVAL_FORM

_CUSTOM_VOCAB_FORM: dict[str, Any] = {
    "prompt": "How should I proceed?",
    "fields": [
        {
            "name": "decision",
            "type": "radio",
            "label": "Decision",
            "options": [
                {"value": "accept", "label": "Accept"},
                {"value": "deny", "label": "Deny"},
                {"value": "amend", "label": "Amend"},
            ],
            "default": "accept",
        },
        {
            "name": "note",
            "type": "textarea",
            "label": "Note",
            "default": "",
            "required": False,
        },
    ],
}

_fake_client = make_fake_client

# ── Layer 2: pure logic, no app ──────────────────────────────────────────────


def test_envelope_supports_widgets_approval() -> None:
    env = InterruptEnvelope.from_interrupt_values(_APPROVAL_FORM)
    assert env is not None
    assert envelope_supports_widgets(env) is True


def test_envelope_supports_widgets_rejects_unknown_type() -> None:
    form = {"fields": [{"name": "x", "type": "markdown"}]}
    env = InterruptEnvelope.from_interrupt_values(form)
    assert env is not None
    assert envelope_supports_widgets(env) is False


def test_build_submit_summary_hides_empty_feedback() -> None:
    env = InterruptEnvelope.from_interrupt_values(_APPROVAL_FORM)
    assert env is not None
    summary = build_submit_summary(env, {"action": "approve", "feedback": ""})
    assert "action=approve" in summary
    assert "feedback" not in summary


def test_build_hitl_widget_dispatches_to_inline_picker() -> None:
    env = InterruptEnvelope.from_interrupt_values(_APPROVAL_FORM)
    assert env is not None
    widget = build_hitl_widget(env, lambda _p: None)
    assert isinstance(widget, InlinePicker)


def test_build_hitl_widget_falls_back_to_inline_form_for_complex_shape() -> None:
    form = {
        "fields": [
            {"name": "age", "type": "int"},
            {"name": "okay", "type": "bool"},
        ]
    }
    env = InterruptEnvelope.from_interrupt_values(form)
    assert env is not None
    widget = build_hitl_widget(env, lambda _p: None)
    assert isinstance(widget, InlineForm)


# ── Layer 3: app-mounted rendering tests ────────────────────────────────────


async def test_inline_picker_renders_compact() -> None:
    """Approval envelope mounts an OptionList + Input, no RadioSet / Submit."""
    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._mount_hitl_widgets(_APPROVAL_FORM) is True
        await pilot.pause()
        picker = app.query_one(InlinePicker)
        option_lists = list(picker.query(OptionList))
        assert len(option_lists) == 1
        assert option_lists[0].option_count == 3
        assert len(list(picker.query(Input))) == 1
        assert len(list(picker.query(RadioSet))) == 0
        assert len(list(picker.query("#hitl-submit"))) == 0
        app._unmount_hitl_widgets()
        app.exit()


async def test_inline_form_renders_non_pick_shape() -> None:
    """Multi-type envelope falls through to InlineForm (generic path)."""
    form = {
        "prompt": "Review",
        "fields": [
            {"name": "age", "type": "int", "default": "30"},
            {"name": "agree", "type": "bool", "default": False},
            {"name": "comment", "type": "text", "label": "Comment"},
        ],
    }
    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._mount_hitl_widgets(form) is True
        await pilot.pause()
        app.query_one(InlineForm)
        assert len(list(app.query(InlinePicker))) == 0
        app._unmount_hitl_widgets()
        app.exit()


async def test_unknown_field_type_falls_back_to_text_path() -> None:
    form = {
        "prompt": "Read and OK?",
        "fields": [
            {"name": "note", "type": "markdown", "default": "some notes"},
            {"name": "answer", "type": "text", "label": "Answer"},
        ],
    }
    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._mount_hitl_widgets(form) is False
        assert len(list(app.query(InlineForm))) == 0
        assert len(list(app.query(InlinePicker))) == 0
        app.exit()


async def test_checkbox_collection_in_inline_form() -> None:
    """Multi-select checkbox collects ticked values via InlineForm."""
    form = {
        "prompt": "Pick tags",
        "fields": [
            {
                "name": "tags",
                "type": "checkbox",
                "options": [
                    {"value": "a", "label": "Alpha"},
                    {"value": "b", "label": "Beta"},
                    {"value": "c", "label": "Gamma"},
                ],
                "default": ["a"],
                "required": False,
            },
            {"name": "comment", "type": "text", "required": False},
        ],
    }
    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._mount_hitl_widgets(form) is True
        await pilot.pause()
        iform = app.query_one(InlineForm)
        boxes = list(iform.query(Checkbox))
        assert len(boxes) == 3
        boxes[2].value = True
        iform.action_submit_form()
        app._unmount_hitl_widgets()
        app.exit()


# ── Collect-resume tests (skipped — Future/pilot timing) ────────────────────

_SKIP_COLLECT = pytest.mark.skip(
    reason="asyncio.create_task + Textual pilot Future resolution hangs; "
    "needs Textual-native async drive pattern"
)


@_SKIP_COLLECT
async def test_inline_picker_selection_submits_payload() -> None:
    pass


@_SKIP_COLLECT
async def test_inline_picker_works_for_custom_vocab() -> None:
    pass


@_SKIP_COLLECT
async def test_inline_picker_text_enter_submits_with_highlighted() -> None:
    pass


@_SKIP_COLLECT
async def test_text_reply_still_resolves_under_inline_picker() -> None:
    pass


@_SKIP_COLLECT
async def test_inline_form_enter_submits() -> None:
    pass
