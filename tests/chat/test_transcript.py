"""Layer 3 tests: MessageBlock, AgentPanel, and Transcript (VerticalScroll)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from monet.cli.chat._agent_panel import AgentPanel
from monet.cli.chat._message_block import MessageBlock
from monet.cli.chat._transcript import Transcript

# ---------------------------------------------------------------------------
# MessageBlock — structural
# ---------------------------------------------------------------------------


def test_message_block_is_static() -> None:
    block = MessageBlock("[user] hello")
    assert isinstance(block, Static)


def test_message_block_stores_line() -> None:
    block = MessageBlock("[assistant] hi", markdown=True)
    assert block._line == "[assistant] hi"
    assert block._markdown is True


def test_message_block_plain_flag_default() -> None:
    block = MessageBlock("[info] starting")
    assert block._markdown is False


# ---------------------------------------------------------------------------
# AgentPanel — model-level (no app required)
# ---------------------------------------------------------------------------


def test_agent_panel_add_creates_node() -> None:
    panel = AgentPanel()
    # Can't call add_agent without a mounted tree, but can verify structure
    assert isinstance(panel, AgentPanel)
    assert panel._agents == {}


def test_agent_panel_is_tree() -> None:
    from textual.widgets import Tree

    panel = AgentPanel()
    assert isinstance(panel, Tree)


# ---------------------------------------------------------------------------
# AgentPanel — mounted
# ---------------------------------------------------------------------------


class _AgentApp(App[None]):
    def compose(self) -> ComposeResult:
        yield AgentPanel(id="agent-panel")

    def on_mount(self) -> None:
        self._panel = self.query_one(AgentPanel)


@pytest.mark.asyncio
async def test_agent_panel_add_agent_registers_node() -> None:
    app = _AgentApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        panel = app.query_one(AgentPanel)
        panel.add_agent("researcher")
        await pilot.pause()
        assert "researcher" in panel._agents
        app.exit()


@pytest.mark.asyncio
async def test_agent_panel_add_agent_idempotent() -> None:
    app = _AgentApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        panel = app.query_one(AgentPanel)
        panel.add_agent("researcher")
        panel.add_agent("researcher")
        await pilot.pause()
        assert len(panel._agents) == 1
        app.exit()


@pytest.mark.asyncio
async def test_agent_panel_update_creates_implicit_agent() -> None:
    app = _AgentApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        panel = app.query_one(AgentPanel)
        panel.update_agent("writer", "drafting outline")
        await pilot.pause()
        assert "writer" in panel._agents
        app.exit()


@pytest.mark.asyncio
async def test_agent_panel_reset_clears_agents() -> None:
    app = _AgentApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        panel = app.query_one(AgentPanel)
        panel.add_agent("researcher")
        panel.add_agent("writer")
        await pilot.pause()
        panel.clear_agents()
        await pilot.pause()
        assert panel._agents == {}
        app.exit()


# ---------------------------------------------------------------------------
# Transcript — VerticalScroll-based
# ---------------------------------------------------------------------------


class _TranscriptApp(App[None]):
    def compose(self) -> ComposeResult:
        yield Transcript(id="transcript")

    def on_mount(self) -> None:
        self._transcript = self.query_one(Transcript)


@pytest.mark.asyncio
async def test_transcript_mounts_vertical_scroll() -> None:
    app = _TranscriptApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        scroll = app.query_one("#_scroll", VerticalScroll)
        assert scroll is not None
        app.exit()


@pytest.mark.asyncio
async def test_transcript_append_creates_message_block() -> None:
    app = _TranscriptApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        t = app.query_one(Transcript)
        t.append("[user] hello")
        await pilot.pause()
        blocks = app.query(MessageBlock)
        assert len(list(blocks)) == 1
        app.exit()


@pytest.mark.asyncio
async def test_transcript_append_single_block_per_call() -> None:
    """Each append() produces exactly one MessageBlock — regression guard."""
    app = _TranscriptApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        t = app.query_one(Transcript)
        t.append("[user] one")
        t.append("[assistant] two")
        t.append("[info] three")
        await pilot.pause()
        blocks = list(app.query(MessageBlock))
        assert len(blocks) == 3
        app.exit()


@pytest.mark.asyncio
async def test_transcript_clear_removes_blocks() -> None:
    app = _TranscriptApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        t = app.query_one(Transcript)
        t.append("[user] one")
        t.append("[user] two")
        await pilot.pause()
        t.clear()
        await pilot.pause()
        blocks = list(app.query(MessageBlock))
        assert len(blocks) == 0
        app.exit()


@pytest.mark.asyncio
async def test_transcript_load_history_creates_one_block_per_message() -> None:
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "bye"},
    ]
    app = _TranscriptApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        t = app.query_one(Transcript)
        t.load_history(history)
        await pilot.pause()
        blocks = list(app.query(MessageBlock))
        assert len(blocks) == 3
        app.exit()


@pytest.mark.asyncio
async def test_transcript_get_text_reflects_appended_lines() -> None:
    app = _TranscriptApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        t = app.query_one(Transcript)
        t.append("[user] hello")
        t.append("[assistant] world")
        await pilot.pause()
        text = t.get_text()
        assert "[user] hello" in text
        assert "[assistant] world" in text
        app.exit()


@pytest.mark.asyncio
async def test_transcript_get_last_assistant() -> None:
    app = _TranscriptApp()
    async with app.run_test(headless=True) as pilot:
        await pilot.pause()
        t = app.query_one(Transcript)
        t.append("[user] question")
        t.append("[assistant] my answer")
        await pilot.pause()
        assert t.get_last_assistant() == "my answer"
        app.exit()
