"""Unit tests for monet.worker.transport._direct callers."""

from __future__ import annotations

import httpx
import pytest
import respx

from monet.core.agent_loader import AgentHTTPRequest, AgentHTTPResponse
from monet.worker.transport._direct import http_caller, openai_caller


def _ctx(task: str = "hello", task_id: str = "t1") -> dict:
    return {
        "task": task,
        "task_id": task_id,
        "context": [],
        "command": "run",
        "agent_id": "agent",
    }


# ── openai_caller ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_openai_caller_happy_path() -> None:
    respx.post("http://localhost:8642/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "answer"}}]},
        )
    )
    caller = openai_caller("http://localhost:8642", None, 30.0)
    result = await caller("hello", _ctx())
    assert result == "answer"


@pytest.mark.asyncio
@respx.mock
async def test_openai_caller_appends_completions_path() -> None:
    route = respx.post("http://host:1/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )
    )
    caller = openai_caller("http://host:1", None, 30.0)
    await caller("q", _ctx())
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_openai_caller_sends_model_when_set() -> None:
    sent: dict = {}

    def capture(req: httpx.Request) -> httpx.Response:
        import json

        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("http://host:1/v1/chat/completions").mock(side_effect=capture)
    caller = openai_caller("http://host:1", "deepseek-v3", 30.0)
    await caller("q", _ctx())
    assert sent.get("model") == "deepseek-v3"


@pytest.mark.asyncio
@respx.mock
async def test_openai_caller_auth_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    headers_sent: dict = {}

    def capture(req: httpx.Request) -> httpx.Response:
        headers_sent.update(dict(req.headers))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("http://host:1/v1/chat/completions").mock(side_effect=capture)
    caller = openai_caller("http://host:1", None, 30.0)
    await caller("q", _ctx())
    assert headers_sent.get("authorization") == "Bearer sk-test"


@pytest.mark.asyncio
@respx.mock
async def test_openai_caller_upstream_error_propagates() -> None:
    respx.post("http://host:1/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "overloaded"})
    )
    caller = openai_caller("http://host:1", None, 30.0)
    with pytest.raises(httpx.HTTPStatusError):
        await caller("q", _ctx())


# ── http_caller ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_http_caller_resolves_jsonpath_body() -> None:
    sent: dict = {}

    def capture(req: httpx.Request) -> httpx.Response:
        import json

        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"output": "pi says hi"})

    respx.post("http://pi:9000/chat").mock(side_effect=capture)
    caller = http_caller(
        "http://pi:9000/chat",
        AgentHTTPRequest(body={"message": "$.task", "sid": "$.task_id"}),
        AgentHTTPResponse(output="$.output"),
        30.0,
    )
    result = await caller("hello", _ctx(task="hello", task_id="tid42"))
    assert sent["message"] == "hello"
    assert sent["sid"] == "tid42"
    assert result == "pi says hi"


@pytest.mark.asyncio
@respx.mock
async def test_http_caller_nested_jsonpath_extraction() -> None:
    respx.post("http://pi:9000/chat").mock(
        return_value=httpx.Response(200, json={"data": {"reply": "nested"}})
    )
    caller = http_caller(
        "http://pi:9000/chat",
        AgentHTTPRequest(body={"q": "$.task"}),
        AgentHTTPResponse(output="$.data.reply"),
        30.0,
    )
    result = await caller("x", _ctx())
    assert result == "nested"


@pytest.mark.asyncio
@respx.mock
async def test_http_caller_literal_body_values() -> None:
    sent: dict = {}

    def capture(req: httpx.Request) -> httpx.Response:
        import json

        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"output": "ok"})

    respx.post("http://pi:9000/chat").mock(side_effect=capture)
    caller = http_caller(
        "http://pi:9000/chat",
        AgentHTTPRequest(body={"prompt": "$.task", "stream": False}),
        AgentHTTPResponse(output="$.output"),
        30.0,
    )
    await caller("q", _ctx())
    assert sent["stream"] is False


@pytest.mark.asyncio
@respx.mock
async def test_http_caller_upstream_error_propagates() -> None:
    respx.post("http://pi:9000/chat").mock(
        return_value=httpx.Response(502, json={"error": "bad gateway"})
    )
    caller = http_caller(
        "http://pi:9000/chat",
        AgentHTTPRequest(body={}),
        AgentHTTPResponse(),
        30.0,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await caller("q", _ctx())
