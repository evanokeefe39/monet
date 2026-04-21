"""Shared fixtures for chat TUI tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


APPROVAL_FORM: dict[str, Any] = {
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


def make_fake_client() -> Any:
    """Build a minimal MonetClient mock for chat TUI tests."""
    client = MagicMock()
    chat = MagicMock()

    async def _send(*_args: Any, **_kwargs: Any) -> AsyncIterator[str]:
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


@pytest.fixture()
def fake_client() -> Any:
    return make_fake_client()
