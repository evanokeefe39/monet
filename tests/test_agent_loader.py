"""Tests for declarative agent loader — config models and handler dispatch."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from pydantic import ValidationError

from monet.core.agent_loader import (
    AgentHTTPRequest,
    AgentHTTPResponse,
    AgentTransportConfig,
    _make_handler,
)

if TYPE_CHECKING:
    from pathlib import Path

# ── AgentTransportConfig validation ─────────────────────────────────────────


def test_openai_transport_valid() -> None:
    t = AgentTransportConfig(protocol="openai", url="http://localhost:8642")
    assert t.protocol == "openai"
    assert t.url == "http://localhost:8642"


def test_http_transport_valid() -> None:
    t = AgentTransportConfig(
        protocol="http",
        url="http://localhost:9000/chat",
        request=AgentHTTPRequest(body={"message": "$.task"}),
        response=AgentHTTPResponse(output="$.message"),
    )
    assert t.protocol == "http"


def test_zeroclaw_transport_no_url_required() -> None:
    t = AgentTransportConfig(protocol="zeroclaw", timeout=120.0)
    assert t.url is None


def test_custom_transport_valid() -> None:
    t = AgentTransportConfig(protocol="custom", adapter="mymodule:my_fn")
    assert t.adapter == "mymodule:my_fn"


def test_openai_missing_url_raises() -> None:
    with pytest.raises(Exception, match="url"):
        AgentTransportConfig(protocol="openai")


def test_http_missing_url_raises() -> None:
    with pytest.raises(Exception, match="url"):
        AgentTransportConfig(protocol="http")


def test_custom_missing_adapter_raises() -> None:
    with pytest.raises(Exception, match="adapter"):
        AgentTransportConfig(protocol="custom")


def test_old_type_field_rejected() -> None:
    """Breaking change: 'type' field no longer accepted."""
    with pytest.raises(ValidationError):
        AgentTransportConfig(type="http", url="http://localhost:9000")  # type: ignore[call-arg]


# ── openai caller via _make_handler ─────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_make_handler_openai_calls_completions() -> None:
    respx.post("http://localhost:8642/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "result text"}}]},
        )
    )
    transport = AgentTransportConfig(protocol="openai", url="http://localhost:8642")
    handler = _make_handler(transport, "hermes", "reason")
    result = await handler("hello")
    assert result == "result text"


@pytest.mark.asyncio
@respx.mock
async def test_make_handler_openai_sends_task_as_user_message() -> None:
    sent: dict = {}

    def capture(req: httpx.Request) -> httpx.Response:
        import json

        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("http://localhost:8642/v1/chat/completions").mock(side_effect=capture)
    transport = AgentTransportConfig(protocol="openai", url="http://localhost:8642")
    handler = _make_handler(transport, "hermes", "reason")
    await handler("do the thing")
    assert sent["messages"] == [{"role": "user", "content": "do the thing"}]


# ── http caller via _make_handler ────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_make_handler_http_resolves_jsonpath_body() -> None:
    sent: dict = {}

    def capture(req: httpx.Request) -> httpx.Response:
        import json

        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"message": "pi response"})

    respx.post("http://localhost:9000/chat").mock(side_effect=capture)
    transport = AgentTransportConfig(
        protocol="http",
        url="http://localhost:9000/chat",
        request=AgentHTTPRequest(body={"message": "$.task"}),
        response=AgentHTTPResponse(output="$.message"),
    )
    handler = _make_handler(transport, "pi", "code")
    result = await handler("write a function")
    assert sent["message"] == "write a function"
    assert result == "pi response"


@pytest.mark.asyncio
@respx.mock
async def test_make_handler_http_extracts_custom_response_path() -> None:
    respx.post("http://localhost:9000/chat").mock(
        return_value=httpx.Response(200, json={"nested": {"reply": "deep value"}})
    )
    transport = AgentTransportConfig(
        protocol="http",
        url="http://localhost:9000/chat",
        request=AgentHTTPRequest(body={"q": "$.task"}),
        response=AgentHTTPResponse(output="$.nested.reply"),
    )
    handler = _make_handler(transport, "pi", "code")
    result = await handler("query")
    assert result == "deep value"


# ── custom caller via _make_handler ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_make_handler_custom_loads_and_calls_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = tmp_path / "myplugin.py"
    plugin.write_text(
        textwrap.dedent(
            """
            async def run(task, url, timeout):
                return f"plugin:{task}"
            """
        )
    )
    import sys

    monkeypatch.syspath_prepend(str(tmp_path))
    transport = AgentTransportConfig(protocol="custom", adapter="myplugin:run")
    handler = _make_handler(transport, "custom_agent", "do")
    result = await handler("hello world")
    assert result == "plugin:hello world"
    sys.modules.pop("myplugin", None)


# ── load_agents integration ──────────────────────────────────────────────────


def test_load_agents_registers_from_toml(tmp_path: Path, clean_registry: None) -> None:
    toml = tmp_path / "agents.toml"
    toml.write_text(
        textwrap.dedent(
            """\
            [[agent]]
            id = "testbot"
            command = "run"
            description = "a test agent"
            transport = { protocol = "openai", url = "http://localhost:1234" }
            """
        )
    )
    from monet.core.agent_loader import load_agents

    count = load_agents(toml)
    assert count == 1

    from monet.core.registry import default_registry

    assert default_registry.exists("testbot", "run")


def test_load_agents_rejects_old_type_field(tmp_path: Path) -> None:
    toml = tmp_path / "agents.toml"
    toml.write_text(
        textwrap.dedent(
            """\
            [[agent]]
            id = "legacy"
            command = "run"
            transport = { type = "http", url = "http://localhost:9000" }
            """
        )
    )
    from monet.core.agent_loader import load_agents

    with pytest.raises(ValueError, match="invalid declaration"):
        load_agents(toml)
