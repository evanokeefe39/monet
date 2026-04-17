"""Fail-fast checks run before ``monet chat`` opens its TUI.

Two failure modes must surface actionable errors instead of a hung
terminal or an opaque server 500:

1. Server unreachable — the preflight ``/health`` probe times out or
   connect-errors.
2. Chat graph not registered — the server is up but the resolved chat
   graph id is absent from ``/api/v1/assistants``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from monet.cli._chat import _chat_main


class _RaisingClient:
    """``httpx.AsyncClient`` stand-in that always connect-errors."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _RaisingClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("nope")


class _OkClient:
    """Preflight-only client — ``/health`` returns 200, nothing else."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _OkClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        class _Resp:
            status_code = 200

        return _Resp()


@pytest.mark.asyncio
async def test_server_unreachable_fails_before_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _RaisingClient)

    with pytest.raises(SystemExit) as exc:
        await _chat_main(
            url="http://127.0.0.1:65535",
            api_key=None,
            force_new=False,
            list_sessions=False,
            resume_id=None,
            session_name=None,
            graph_override=None,
        )
    assert exc.value.code == 2


@pytest.mark.asyncio
async def test_chat_graph_missing_fails_before_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _OkClient)

    # Stub MonetClient so list_graphs reports a roster without "chat".
    class _StubClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def list_graphs(self) -> list[str]:
            return ["custom_pipeline", "other"]

    monkeypatch.setattr("monet.client.MonetClient", _StubClient)

    with pytest.raises(SystemExit) as exc:
        await _chat_main(
            url="http://127.0.0.1:2026",
            api_key=None,
            force_new=False,
            list_sessions=False,
            resume_id=None,
            session_name=None,
            graph_override=None,
        )
    assert exc.value.code == 2


@pytest.mark.asyncio
async def test_list_graphs_error_fails_before_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _OkClient)

    class _BrokenClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def list_graphs(self) -> list[str]:
            raise RuntimeError("boom")

    monkeypatch.setattr("monet.client.MonetClient", _BrokenClient)

    with pytest.raises(SystemExit) as exc:
        await _chat_main(
            url="http://127.0.0.1:2026",
            api_key=None,
            force_new=False,
            list_sessions=False,
            resume_id=None,
            session_name=None,
            graph_override=None,
        )
    assert exc.value.code == 2
