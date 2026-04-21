"""Smoke + component tests for the Textual chat app."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import pytest

pytest.importorskip("textual")

from monet.cli.chat import ChatApp
from monet.cli.chat._hitl import (
    format_form_prompt as _format_form_prompt,
)
from monet.cli.chat._hitl import (
    is_approval_form as _is_approval_form,
)
from monet.cli.chat._hitl import (
    parse_approval_reply as _parse_approval_reply,
)
from monet.cli.chat._hitl import (
    parse_text_reply as _parse_text_reply,
)
from monet.cli.chat._messages import PromptSubmitted
from monet.cli.chat._prompt import AutoGrowTextArea
from monet.cli.chat._slash import RegistrySuggester
from monet.cli.chat._view import format_progress_line as _format_progress_line
from monet.client._events import AgentProgress
from tests.chat.conftest import APPROVAL_FORM, make_fake_client

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


# --- HITL form-text parser -----------------------------------------------


_APPROVAL_FORM = APPROVAL_FORM


def test_is_approval_form_detects_action_radio() -> None:
    assert _is_approval_form(_APPROVAL_FORM) is True


def test_is_approval_form_rejects_other_shapes() -> None:
    other = {
        "fields": [{"name": "answer", "type": "text"}],
    }
    assert _is_approval_form(other) is False


def test_format_form_prompt_approval_includes_help_line() -> None:
    lines = _format_form_prompt(_APPROVAL_FORM)
    assert any("Approve plan?" in line for line in lines)
    assert any("approve | revise" in line for line in lines)


def test_format_form_prompt_single_field_uses_label() -> None:
    form: dict[str, Any] = {
        "prompt": "What's your favourite colour?",
        "fields": [{"name": "answer", "type": "text", "label": "Colour"}],
    }
    lines = _format_form_prompt(form)
    joined = "\n".join(lines)
    assert "favourite colour" in joined
    assert "your colour" in joined


def test_format_form_prompt_multi_field_lists_labels() -> None:
    form: dict[str, Any] = {
        "prompt": "Two questions",
        "fields": [
            {"name": "q0", "type": "text", "label": "First?"},
            {"name": "q1", "type": "text", "label": "Second?"},
        ],
    }
    lines = _format_form_prompt(form)
    joined = "\n".join(lines)
    assert "one line per field" in joined
    assert "First?" in joined
    assert "Second?" in joined


def test_parse_approval_reply_recognises_action_keywords() -> None:
    assert _parse_approval_reply("approve") == {"action": "approve", "feedback": ""}
    assert _parse_approval_reply("yes") == {"action": "approve", "feedback": ""}
    assert _parse_approval_reply("reject") == {"action": "reject", "feedback": ""}
    assert _parse_approval_reply("revise add a step") == {
        "action": "revise",
        "feedback": "add a step",
    }


def test_parse_approval_reply_unknown_returns_none() -> None:
    assert _parse_approval_reply("aprove") is None
    assert _parse_approval_reply("please add detail") is None


def test_parse_text_reply_approval_form_parses_action() -> None:
    payload = _parse_text_reply(_APPROVAL_FORM, "approve")
    assert payload == {"action": "approve", "feedback": ""}


def test_parse_text_reply_approval_form_unknown_returns_none() -> None:
    assert _parse_text_reply(_APPROVAL_FORM, "aprove") is None


def test_parse_text_reply_single_field_takes_whole_text() -> None:
    form: dict[str, Any] = {
        "fields": [{"name": "answer", "type": "text"}],
    }
    assert _parse_text_reply(form, "blue") == {"answer": "blue"}


def test_parse_text_reply_carries_hidden_defaults() -> None:
    form: dict[str, Any] = {
        "fields": [
            {"name": "answer", "type": "text"},
            {"name": "run_id", "type": "hidden", "default": "abc-123"},
        ],
    }
    assert _parse_text_reply(form, "blue") == {"answer": "blue", "run_id": "abc-123"}


# --- Resume integration via prompt --------------------------------------


_fake_client = make_fake_client


def _submit_text(app: ChatApp, text: str) -> None:
    """Simulate a prompt submission by posting PromptSubmitted directly."""
    prompt = app.query_one("#prompt", AutoGrowTextArea)
    prompt.post_message(PromptSubmitted(text))


# --- Progress rendering --------------------------------------------------


def test_format_progress_line_default() -> None:
    line = _format_progress_line(
        AgentProgress(run_id="", agent_id="researcher", status="searching with Exa")
    )
    assert line == "│ searching with Exa"


def test_format_progress_line_with_command() -> None:
    line = _format_progress_line(
        AgentProgress(
            run_id="", agent_id="researcher", status="searching", command="deep"
        )
    )
    assert line == "│ searching"


def test_format_progress_line_empty_status_uses_placeholder() -> None:
    line = _format_progress_line(
        AgentProgress(run_id="", agent_id="planner", status="")
    )
    assert line == "│ ..."


def test_format_progress_line_started_suppressed() -> None:
    line = _format_progress_line(
        AgentProgress(run_id="", agent_id="planner", status="agent:started")
    )
    assert line is None


def test_format_progress_line_completed_suppressed() -> None:
    line = _format_progress_line(
        AgentProgress(run_id="", agent_id="planner", status="agent:completed")
    )
    assert line is None


def test_format_progress_line_failed_red() -> None:
    line = _format_progress_line(
        AgentProgress(
            run_id="",
            agent_id="planner",
            status="agent:failed",
            reasons="429 rate limit",
        )
    )
    assert line == "error: 429 rate limit"


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
        prompt = app.query_one("#prompt", AutoGrowTextArea)
        assert prompt is not None
        app.exit()


async def test_chat_app_submits_message_and_streams() -> None:
    client = MagicMock()
    chat = MagicMock()

    async def _send(thread_id: str, message: str) -> AsyncIterator[str]:
        assert thread_id == "t-1"
        assert message == "hi"
        yield "hello"
        yield " world"

    chat.send_message = _send
    chat._chat_graph_id = "chat"
    chat.get_chat_interrupt = AsyncMock(return_value=None)
    client.chat = chat
    client.slash_commands = AsyncMock(return_value=["/plan"])
    client.list_capabilities = AsyncMock(return_value=[])
    client.list_artifacts = AsyncMock(return_value=[])

    app = ChatApp(client=client, thread_id="t-1", slash_commands=["/plan"])
    async with app.run_test() as pilot:
        await pilot.pause()
        _submit_text(app, "hi")
        await pilot.pause()


async def test_chat_app_opens_runs_screen() -> None:
    from monet.cli.chat._screens import RunsScreen

    client = _fake_client()
    client.list_runs = AsyncMock(return_value=[])

    app = ChatApp(client=client, thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_open_runs()
        await pilot.pause()
        assert isinstance(app.screen, RunsScreen)
        app.exit()


async def test_chat_app_quit_slash_exits() -> None:
    client = _fake_client()
    app = ChatApp(client=client, thread_id="t-1", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        _submit_text(app, "/quit")
        await pilot.pause()
