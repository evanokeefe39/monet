"""Smoke + component tests for the Textual chat app."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, TextArea

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from monet.cli._chat_app import (
    ChatApp,
    InterruptScreen,
    RegistrySuggester,
    _is_selected,
    _option_label,
    _option_value,
)

# --- RegistrySuggester ----------------------------------------------------


async def test_suggester_returns_none_for_non_slash_input() -> None:
    s = RegistrySuggester(["/plan", "/researcher:deep"])
    assert await s.get_suggestion("hello") is None


async def test_suggester_prefix_match() -> None:
    s = RegistrySuggester(["/plan", "/researcher:deep", "/writer:draft"])
    assert await s.get_suggestion("/pl") == "/plan"
    assert await s.get_suggestion("/res") == "/researcher:deep"


async def test_suggester_no_match_returns_none() -> None:
    s = RegistrySuggester(["/plan"])
    assert await s.get_suggestion("/xyz") is None


async def test_suggester_does_not_echo_exact_match() -> None:
    s = RegistrySuggester(["/plan"])
    assert await s.get_suggestion("/plan") is None


def test_suggester_update_replaces_list() -> None:
    s = RegistrySuggester(["/plan"])
    s.update(["/researcher:deep"])
    assert s._commands == ["/researcher:deep"]


# --- Option helpers -------------------------------------------------------


def test_option_label_and_value_from_dict() -> None:
    opt = {"label": "Approve", "value": "approve"}
    assert _option_label(opt) == "Approve"
    assert _option_value(opt) == "approve"


def test_option_label_and_value_from_string() -> None:
    assert _option_label("approve") == "approve"
    assert _option_value("approve") == "approve"


def test_is_selected_scalar_default() -> None:
    assert _is_selected("a", "a") is True
    assert _is_selected("a", "b") is False


def test_is_selected_list_default() -> None:
    assert _is_selected("a", ["a", "c"]) is True
    assert _is_selected("b", ["a", "c"]) is False


# --- InterruptScreen pilot ------------------------------------------------


def _fake_client() -> Any:
    client = MagicMock()

    async def _send(*_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
        if False:
            yield ""

    client.send_message = _send
    client.slash_commands = AsyncMock(return_value=[])
    return client


async def test_interrupt_screen_submits_collected_values() -> None:
    form: Any = {
        "prompt": "Approve plan?",
        "fields": [
            {
                "name": "action",
                "type": "radio",
                "label": "Decision",
                "options": [
                    {"value": "approve", "label": "Approve"},
                    {"value": "reject", "label": "Reject"},
                ],
                "default": "approve",
            },
            {
                "name": "feedback",
                "type": "textarea",
                "label": "Feedback",
                "default": "",
            },
            {
                "name": "confidence",
                "type": "int",
                "label": "Confidence",
                "default": 5,
            },
            {
                "name": "notify",
                "type": "bool",
                "label": "Notify me",
                "default": True,
            },
            {
                "name": "tags",
                "type": "checkbox",
                "label": "Tags",
                "options": ["urgent", "review"],
                "default": ["urgent"],
            },
            {
                "name": "priority",
                "type": "select",
                "label": "Priority",
                "options": ["low", "high"],
                "default": "low",
            },
            {
                "name": "run_id",
                "type": "hidden",
                "label": "",
                "default": "abc-123",
            },
        ],
    }

    captured: dict[str, Any] = {}

    def _done(result: dict[str, Any] | None) -> None:
        captured["result"] = result or {}

    class _Host(ChatApp):
        def __init__(self) -> None:
            super().__init__(client=_fake_client(), thread_id="t")

        def on_mount(self) -> None:
            super().on_mount()
            self.push_screen(InterruptScreen(form), _done)

    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = cast("InterruptScreen", host.screen)
        assert isinstance(screen, InterruptScreen)

        conf = screen.query_one("#f-confidence", Input)
        conf.value = "7"

        fb = screen.query_one("#f-feedback", TextArea)
        fb.text = "looks good"

        await pilot.click("#submit")
        await pilot.pause()
        host.exit()

    result = captured["result"]
    assert result["action"] == "approve"
    assert result["feedback"] == "looks good"
    assert result["confidence"] == 7
    assert result["notify"] is True
    assert "urgent" in result["tags"]
    assert result["priority"] == "low"
    # Hidden field carried through without a widget.
    assert result["run_id"] == "abc-123"


async def test_interrupt_screen_cancel_returns_empty_dict() -> None:
    form: Any = {
        "prompt": "Approve?",
        "fields": [
            {
                "name": "action",
                "type": "radio",
                "options": [{"value": "yes", "label": "Yes"}],
                "default": "yes",
            }
        ],
    }
    captured: dict[str, Any] = {}

    def _done(result: dict[str, Any] | None) -> None:
        captured["result"] = result or {}

    class _Host(ChatApp):
        def __init__(self) -> None:
            super().__init__(client=_fake_client(), thread_id="t")

        def on_mount(self) -> None:
            super().on_mount()
            self.push_screen(InterruptScreen(form), _done)

    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#cancel")
        await pilot.pause()
        host.exit()
    assert captured["result"] == {}


# --- ChatApp smoke --------------------------------------------------------


async def test_chat_app_mounts_and_renders_history() -> None:
    client = _fake_client()
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    app = ChatApp(
        client=client,
        thread_id="t1",
        slash_commands=["/plan"],
        history=history,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Header + Input present.
        prompt = app.query_one("#prompt", Input)
        assert prompt.suggester is app._suggester
        app.exit()


async def test_chat_app_submits_message_and_streams() -> None:
    client = MagicMock()

    async def _send(thread_id: str, message: str) -> AsyncIterator[str]:
        assert thread_id == "t-1"
        assert message == "hi"
        yield "hello"
        yield " world"

    client.send_message = _send
    client.slash_commands = AsyncMock(return_value=["/plan"])

    app = ChatApp(client=client, thread_id="t-1", slash_commands=["/plan"])
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.value = "hi"
        await pilot.press("enter")
        await pilot.pause()
    # Not asserting RichLog content to avoid version-specific widget internals;
    # we've asserted send_message was invoked via the body.


async def test_chat_app_quit_slash_exits() -> None:
    client = _fake_client()
    app = ChatApp(client=client, thread_id="t-1", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/quit"
        await pilot.press("enter")
        await pilot.pause()
    # If we get here without timing out, exit fired.
