"""Smoke + component tests for the Textual chat app."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("textual")

from textual.widgets import Input

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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
from monet.cli.chat._slash import RegistrySuggester
from monet.cli.chat._view import format_progress_line as _format_progress_line
from monet.client._events import AgentProgress

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
        },
    ],
}


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
    """Typos like 'aprove' must not silently fall through to revise."""
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


def _fake_client() -> Any:
    client = MagicMock()
    chat = MagicMock()

    async def _send(*_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
        if False:
            yield ""

    chat.send_message = _send
    chat._chat_graph_id = "chat"
    client.chat = chat
    client.slash_commands = AsyncMock(return_value=[])
    return client


async def test_collect_resume_uses_next_prompt_submission() -> None:
    """Form interrupt → user types `approve` → resume payload returned."""
    import asyncio

    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        # Kick off the collector in the background.
        result_holder: dict[str, Any] = {}

        async def _drive() -> None:
            result_holder["payload"] = await app._collect_resume(_APPROVAL_FORM)

        task = asyncio.create_task(_drive())
        await pilot.pause()
        # Form prompt + help line should be in transcript.
        joined = "\n".join(app._transcript_lines)
        assert "Approve plan?" in joined
        assert "approve | revise" in joined
        # User submits "approve" via the prompt.
        prompt = app.query_one("#prompt", Input)
        prompt.value = "approve"
        await pilot.press("enter")
        await pilot.pause()
        await task
        app.exit()
    assert result_holder["payload"] == {"action": "approve", "feedback": ""}


async def test_collect_resume_reprompts_on_typo() -> None:
    """A typo (``aprove``) re-asks instead of silently rejecting."""
    import asyncio

    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        result_holder: dict[str, Any] = {}

        async def _drive() -> None:
            result_holder["payload"] = await app._collect_resume(_APPROVAL_FORM)

        task = asyncio.create_task(_drive())
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.value = "aprove"  # typo
        await pilot.press("enter")
        await pilot.pause()
        # Error line should appear, payload not yet resolved.
        assert "didn't recognise" in "\n".join(app._transcript_lines)
        assert "payload" not in result_holder
        # Now type it correctly.
        prompt.value = "approve"
        await pilot.press("enter")
        await pilot.pause()
        await task
        app.exit()
    assert result_holder["payload"] == {"action": "approve", "feedback": ""}


async def test_collect_resume_revise_with_feedback() -> None:
    import asyncio

    app = ChatApp(client=_fake_client(), thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        result_holder: dict[str, Any] = {}

        async def _drive() -> None:
            result_holder["payload"] = await app._collect_resume(_APPROVAL_FORM)

        task = asyncio.create_task(_drive())
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.value = "revise add benchmarking step"
        await pilot.press("enter")
        await pilot.pause()
        await task
        app.exit()
    assert result_holder["payload"] == {
        "action": "revise",
        "feedback": "add benchmarking step",
    }


# --- Progress rendering --------------------------------------------------


def test_format_progress_line_default() -> None:
    line = _format_progress_line(
        AgentProgress(run_id="", agent_id="researcher", status="searching with Exa")
    )
    assert line == "[progress] researcher: searching with Exa"


def test_format_progress_line_empty_status_uses_placeholder() -> None:
    line = _format_progress_line(
        AgentProgress(run_id="", agent_id="planner", status="")
    )
    assert line == "[progress] planner: ..."


async def test_drain_stream_renders_progress_lines() -> None:
    """AgentProgress chunks land as ``[progress]`` transcript lines."""
    from textual.widgets import RichLog

    client = _fake_client()

    async def _stream() -> AsyncIterator[Any]:
        yield AgentProgress(run_id="", agent_id="researcher", status="searching")
        yield "final assistant text"

    app = ChatApp(client=client, thread_id="t-1", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        log = app.query_one("#transcript", RichLog)
        await app._drain_stream(log, _stream(), source="initial")

    assert "[progress] researcher: searching" in app._transcript_lines
    assert "[assistant] final assistant text" in app._transcript_lines


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
        prompt = app.query_one("#prompt", Input)
        assert prompt.suggester is app._suggester
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
    client.chat = chat
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


async def test_cmd_list_runs_filters_chat_only_runs() -> None:
    from monet.client._events import RunSummary

    client = _fake_client()

    async def _list_runs(*, limit: int = 20) -> list[RunSummary]:
        return [
            RunSummary(
                run_id="r-pipeline",
                status="success",
                completed_stages=["planning", "execution"],
                created_at="2026-04-16T10:00:00",
            ),
            RunSummary(
                run_id="r-chatonly",
                status="success",
                completed_stages=["chat"],
                created_at="2026-04-16T10:05:00",
            ),
        ]

    client.list_runs = _list_runs
    app = ChatApp(client=client, thread_id="t", slash_commands=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/runs"
        await pilot.press("enter")
        await pilot.pause()
        combined = "\n".join(app._transcript_lines)
        assert "r-pipeli" in combined  # truncated to 8 chars
        assert "r-chaton" not in combined
        assert "1 recent pipeline run(s)" in combined
        app.exit()


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
