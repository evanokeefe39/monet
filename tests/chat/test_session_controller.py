"""Proof-of-concept and Layer 3 tests for SessionController."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.chat.conftest import make_fake_client


def _make_client() -> Any:
    client = make_fake_client()
    client.chat.get_thread_transcript = AsyncMock(return_value=[])
    client.chat.get_chat_history = AsyncMock(return_value=[])
    client.chat.get_chat_name = AsyncMock(return_value="test-thread")
    client.chat.create_chat = AsyncMock(return_value="new-thread-id")
    client.chat.count_thread_runs = AsyncMock(return_value=0)
    return client


@pytest.mark.asyncio
async def test_session_controller_mounts() -> None:
    """SessionController mounts inside a minimal App without error."""
    from textual.app import App, ComposeResult
    from textual.widgets import Label

    from monet.cli.chat._session import SessionController
    from monet.cli.chat._status_bar import StatusBar
    from monet.cli.chat._transcript import Transcript

    client = _make_client()

    class _MinimalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield SessionController(
                client=client,
                initial_thread_id="",
                server_slash_commands=[],
                initial_transcript=[],
                id="session",
            )
            yield Transcript(id="transcript")
            yield Label("prompt", id="prompt")
            yield StatusBar(id="status-bar")

        def on_mount(self) -> None:
            from monet.cli.chat._session import SessionController

            session = self.query_one(SessionController)
            session.setup(
                transcript=self.query_one("#transcript", Transcript),
                status_bar=self.query_one("#status-bar", StatusBar),
                prompt=self.query_one("#prompt"),
                slash_suggest=None,
            )

    async with _MinimalApp().run_test() as pilot:
        from monet.cli.chat._session import SessionController

        session = pilot.app.query_one(SessionController)
        assert session is not None


@pytest.mark.asyncio
async def test_prompt_submitted_delegates_to_session() -> None:
    """App.on_prompt_submitted delegates to SessionController."""
    from monet.cli.chat._app import ChatApp
    from monet.cli.chat._messages import PromptSubmitted
    from monet.cli.chat._session import SessionController

    client = _make_client()

    received: list[str] = []

    async with ChatApp(
        client=client,
        thread_id="",
        slash_commands=[],
        transcript=[],
    ).run_test() as pilot:
        session = pilot.app.query_one(SessionController)

        # Patch handle_prompt_submitted so we capture calls without running a real turn
        def _capture(event: PromptSubmitted) -> None:
            received.append(event.text)

        session.handle_prompt_submitted = _capture  # type: ignore[method-assign]

        pilot.app.post_message(PromptSubmitted(text="hello"))
        await pilot.pause(delay=0.1)

    assert received == ["hello"]


@pytest.mark.asyncio
async def test_cancel_run_clears_worker() -> None:
    """cancel_run() cancels the active turn worker and sets busy=False."""
    from monet.cli.chat._app import ChatApp
    from monet.cli.chat._session import SessionController

    client = _make_client()

    async with ChatApp(
        client=client,
        thread_id="",
        slash_commands=[],
        transcript=[],
    ).run_test() as pilot:
        session = pilot.app.query_one(SessionController)
        # Inject a fake worker
        fake_worker = MagicMock()
        fake_worker.cancel = MagicMock()
        session._turn_worker = fake_worker
        pilot.app.busy = True

        session.cancel_run()
        await pilot.pause(delay=0.05)

        assert fake_worker.cancel.called
        assert session._turn_worker is None


@pytest.mark.asyncio
async def test_switch_thread_updates_app_state() -> None:
    """switch_thread() posts ThreadSet so App.thread_id is updated."""
    from monet.cli.chat._app import ChatApp
    from monet.cli.chat._session import SessionController

    client = _make_client()

    async with ChatApp(
        client=client,
        thread_id="old-id",
        slash_commands=[],
        transcript=[],
    ).run_test() as pilot:
        app = pilot.app
        session = app.query_one(SessionController)

        await session.switch_thread("new-thread")
        await pilot.pause(delay=0.1)

        assert app.thread_id == "new-thread"
