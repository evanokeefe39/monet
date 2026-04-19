"""Verify make_client / MonetClient thread the API key as a Bearer header."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    import pytest


def test_make_client_sends_bearer_when_key_explicit() -> None:
    from monet.client._wire import make_client

    with patch("langgraph_sdk.get_client") as mock_get:
        make_client("http://example:2026", api_key="secret")

    assert mock_get.call_count == 1
    kwargs = mock_get.call_args.kwargs
    assert kwargs["url"] == "http://example:2026"
    assert kwargs["headers"] == {"Authorization": "Bearer secret"}


def test_make_client_sends_bearer_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from monet.client._wire import make_client

    monkeypatch.setenv("MONET_API_KEY", "env-secret")
    monkeypatch.setenv("MONET_SERVER_URL", "http://from-env:2026")

    with patch("langgraph_sdk.get_client") as mock_get:
        make_client()

    kwargs = mock_get.call_args.kwargs
    assert kwargs["url"] == "http://from-env:2026"
    assert kwargs["headers"] == {"Authorization": "Bearer env-secret"}


def test_make_client_no_headers_when_key_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from monet.client._wire import make_client

    monkeypatch.delenv("MONET_API_KEY", raising=False)

    with patch("langgraph_sdk.get_client") as mock_get:
        make_client("http://example:2026")

    kwargs = mock_get.call_args.kwargs
    assert kwargs["headers"] is None


def test_monetclient_explicit_key_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from monet.client import MonetClient

    monkeypatch.setenv("MONET_API_KEY", "env-key")

    with patch("langgraph_sdk.get_client") as mock_get:
        MonetClient("http://example:2026", api_key="explicit-key")

    kwargs = mock_get.call_args.kwargs
    assert kwargs["headers"] == {"Authorization": "Bearer explicit-key"}


def test_monetclient_url_default_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from monet.client import MonetClient

    monkeypatch.setenv("MONET_SERVER_URL", "http://prod.example:9999")
    monkeypatch.delenv("MONET_API_KEY", raising=False)

    with patch("langgraph_sdk.get_client") as mock_get:
        MonetClient()

    kwargs = mock_get.call_args.kwargs
    assert kwargs["url"] == "http://prod.example:9999"
    assert kwargs["headers"] is None
