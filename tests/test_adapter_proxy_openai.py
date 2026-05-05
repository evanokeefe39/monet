from __future__ import annotations

import httpx
import pytest
import respx

from monet.adapter._config import AdapterConfig
from monet.adapter._errors import AdapterError
from monet.adapter._proxy_openai import OpenAIProxy, _normalize_url
from monet.adapter._types import TaskRequest


def _config(**kwargs) -> AdapterConfig:
    return AdapterConfig(name="t", type="openai", url="http://localhost:8642", **kwargs)


def _request(task: str = "hello") -> TaskRequest:
    return TaskRequest(task_id="t1", task=task, payload={"task": task})


def test_normalize_no_path() -> None:
    assert (
        _normalize_url("http://localhost:8642")
        == "http://localhost:8642/v1/chat/completions"
    )


def test_normalize_v1_path() -> None:
    assert (
        _normalize_url("http://localhost:8642/v1")
        == "http://localhost:8642/v1/chat/completions"
    )


def test_normalize_custom_path_unchanged() -> None:
    assert (
        _normalize_url("http://localhost:8642/custom") == "http://localhost:8642/custom"
    )


@pytest.mark.asyncio
@respx.mock
async def test_happy_path() -> None:
    respx.post("http://localhost:8642/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "world"}}]},
        )
    )
    proxy = OpenAIProxy(_config())
    result = await proxy.handle_task(_request())
    assert result.output == "world"


@pytest.mark.asyncio
@respx.mock
async def test_auth_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    sent_headers: dict = {}

    def capture(req: httpx.Request) -> httpx.Response:
        sent_headers.update(dict(req.headers))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("http://localhost:8642/v1/chat/completions").mock(side_effect=capture)
    proxy = OpenAIProxy(_config())
    await proxy.handle_task(_request())
    assert sent_headers.get("authorization") == "Bearer sk-test"


@pytest.mark.asyncio
@respx.mock
async def test_explicit_auth_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    sent_headers: dict = {}

    def capture(req: httpx.Request) -> httpx.Response:
        sent_headers.update(dict(req.headers))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("http://localhost:8642/v1/chat/completions").mock(side_effect=capture)
    proxy = OpenAIProxy(_config(auth="Bearer sk-explicit"))
    await proxy.handle_task(_request())
    assert sent_headers.get("authorization") == "Bearer sk-explicit"


@pytest.mark.asyncio
@respx.mock
async def test_model_included_when_set() -> None:
    sent_body: dict = {}

    def capture(req: httpx.Request) -> httpx.Response:
        import json

        sent_body.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx.post("http://localhost:8642/v1/chat/completions").mock(side_effect=capture)
    proxy = OpenAIProxy(_config(model="gpt-4"))
    await proxy.handle_task(_request())
    assert sent_body.get("model") == "gpt-4"


@pytest.mark.asyncio
@respx.mock
async def test_upstream_error_raises_adapter_error() -> None:
    respx.post("http://localhost:8642/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "overloaded"})
    )
    proxy = OpenAIProxy(_config())
    with pytest.raises(AdapterError) as exc_info:
        await proxy.handle_task(_request())
    assert exc_info.value.code == "UPSTREAM_ERROR"
