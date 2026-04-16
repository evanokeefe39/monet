"""CLI-boundary tests for ``monet chat``."""

from __future__ import annotations

from unittest.mock import patch

import httpx
from click.testing import CliRunner

from monet.cli._chat import chat


def test_chat_friendly_error_when_server_unreachable() -> None:
    """``httpx.ConnectError`` becomes a one-line user message + exit 2."""
    runner = CliRunner()
    with patch(
        "monet.cli._chat._chat_main",
        side_effect=httpx.ConnectError("All connection attempts failed"),
    ):
        result = runner.invoke(chat, ["--url", "http://localhost:65000"])
    assert result.exit_code == 2
    assert "Cannot reach monet server" in result.output
    assert "monet dev" in result.output
    # No raw traceback should leak through.
    assert "Traceback" not in result.output
