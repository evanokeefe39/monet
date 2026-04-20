"""Inline HITL widget tests — covers the non-typing resume path."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("textual")

from textual.widgets import Checkbox, Input, OptionList, RadioSet

from monet.cli.chat import ChatApp
from monet.cli.chat._hitl_form import (
    InlineForm,
    InlinePicker,
    build_hitl_widget,
    build_submit_summary,
    envelope_supports_widgets,
)
from monet.cli.chat._messages import PromptSubmitted
from monet.cli.chat._prompt import AutoGrowTextArea
from monet.types import InterruptEnvelope

_APPROVAL_FORM: dict[str, Any] = {
    "prompt": "Approve plan?",
    "fields": [
        {
            "name": "action",
            "type": "radio",
            "label": "Decision",
            "options": [
                {"value": "approve", "label": "Approve"},
                {"value": "revise", "label": "Revise with feedback"},
                {"value": "reject", "label": "Reject"},
            ],
            "default": "approve",
        },
        {
            "name": "feedback",
            "type": "textarea",
            "label": "Feedback (required for revise)",
            "default": "",
            "required": False,
        },
    ],
}

# Structurally identical to the approval form but with a completely
# different vocabulary — no "approve"/"reject"/"revise" or "action"/
# "feedback" anywhere. Load-bearing for the decoupling claim.
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


def _fake_client() -> Any:
    client = MagicMock()
    chat = MagicMock()

    async def _send(*_args: Any, **_kwargs: Any):
        if False:
            yield ""

    chat.send_message = _send
    chat._chat_graph_id = "chat"
    chat.get_chat_interrupt = AsyncMock(return_value=None)
    client.chat = chat
    client.slash_commands = AsyncMock(return_value=[])
    client.list_capabilities = AsyncMock(return_value=[])
    client.list_artifacts = AsyncMock(return_value=[])
    return client


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


async def test_inline_picker_selection_submits_payload() -> None:
    """OptionSelected → consume_payload fires with envelope-keyed payload."""
    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        result: dict[str, Any] = {}

        async def _drive() -> None:
            result["payload"] = await app._collect_resume(_APPROVAL_FORM)

        task = asyncio.create_task(_drive())
        await pilot.pause()
        picker = app.query_one(InlinePicker)
        option_list = picker.query_one(OptionList)
        # Highlight option 1 (revise) and fire Enter to submit.
        option_list.highlighted = 1
        feedback = picker.query_one("#picker-text", Input)
        feedback.value = "tighten scope"
        # Simulate the OptionSelected event the list would post on Enter.
        option_list.action_select()
        await pilot.pause()
        await task
        app.exit()
    assert result["payload"] == {
        "action": "revise",
        "feedback": "tighten scope",
    }


async def test_inline_picker_works_for_custom_vocab() -> None:
    """Decoupling guard: same UX with different keys + option values."""
    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        result: dict[str, Any] = {}

        async def _drive() -> None:
            result["payload"] = await app._collect_resume(_CUSTOM_VOCAB_FORM)

        task = asyncio.create_task(_drive())
        await pilot.pause()
        picker = app.query_one(InlinePicker)
        option_list = picker.query_one(OptionList)
        option_list.highlighted = 2  # "amend"
        picker.query_one("#picker-text", Input).value = "reduce scope"
        option_list.action_select()
        await pilot.pause()
        await task
        app.exit()
    # Payload keys come from the envelope, not from TUI constants.
    assert result["payload"] == {"decision": "amend", "note": "reduce scope"}
    # Absolutely no planner vocabulary in the payload.
    assert "action" not in result["payload"]
    assert "feedback" not in result["payload"]


async def test_inline_picker_text_enter_submits_with_highlighted() -> None:
    """Enter in the free-text Input submits using the highlighted option."""
    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        result: dict[str, Any] = {}

        async def _drive() -> None:
            result["payload"] = await app._collect_resume(_APPROVAL_FORM)

        task = asyncio.create_task(_drive())
        await pilot.pause()
        picker = app.query_one(InlinePicker)
        option_list = picker.query_one(OptionList)
        option_list.highlighted = 2  # reject
        feedback = picker.query_one("#picker-text", Input)
        feedback.value = "not now"
        feedback.action_submit()
        await pilot.pause()
        await task
        app.exit()
    assert result["payload"] == {"action": "reject", "feedback": "not now"}


async def test_text_reply_still_resolves_under_inline_picker() -> None:
    """Typing a reply into the prompt still works when picker is mounted."""
    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        result: dict[str, Any] = {}

        async def _drive() -> None:
            result["payload"] = await app._collect_resume(_APPROVAL_FORM)

        task = asyncio.create_task(_drive())
        await pilot.pause()
        assert len(list(app.query(InlinePicker))) == 1
        prompt = app.query_one("#prompt", AutoGrowTextArea)
        prompt.post_message(PromptSubmitted("reject"))
        await pilot.pause()
        await task
        app.exit()
    assert result["payload"] == {"action": "reject", "feedback": ""}


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


async def test_inline_form_enter_submits() -> None:
    """InlineForm Enter key routes through on_submit callback."""
    form = {
        "prompt": "Quick form",
        "fields": [{"name": "answer", "type": "text", "required": False}],
    }
    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        result: dict[str, Any] = {}

        async def _drive() -> None:
            result["payload"] = await app._collect_resume(form)

        task = asyncio.create_task(_drive())
        await pilot.pause()
        widget = app.query_one(InlineForm)
        widget.action_submit_form()
        await pilot.pause()
        await task
        app.exit()
    assert result["payload"] == {"answer": ""}
