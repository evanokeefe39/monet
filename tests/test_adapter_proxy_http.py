from __future__ import annotations

import httpx
import pytest
import respx

from monet.adapter._config import AdapterConfig, RequestConfig, ResponseConfig
from monet.adapter._errors import AdapterError
from monet.adapter._proxy_http import HTTPProxy, _resolve_body
from monet.adapter._types import TaskRequest


def _config(**kwargs) -> AdapterConfig:
    kwargs.setdefault("response", ResponseConfig(output="$.message"))
    return AdapterConfig(
        name="t",
        type="http",
        url="http://localhost:9000/chat",
        **kwargs,
    )


def _request(task: str = "hi", task_id: str = "t1") -> TaskRequest:
    return TaskRequest(task_id=task_id, task=task, payload={"task": task})


def test_resolve_body_jsonpath() -> None:
    template = {"message": "$.payload.task", "session": "$.task_id"}
    incoming = {"task_id": "abc", "payload": {"task": "hello"}}
    result = _resolve_body(template, incoming)
    assert result == {"message": "hello", "session": "abc"}


def test_resolve_body_literal_passthrough() -> None:
    template = {"stream": False, "max_tokens": 512}
    result = _resolve_body(template, {})
    assert result == {"stream": False, "max_tokens": 512}


def test_resolve_body_nested() -> None:
    template = {"config": {"temperature": 0.2, "prompt": "$.payload.task"}}
    incoming = {"payload": {"task": "go"}}
    result = _resolve_body(template, incoming)
    assert result == {"config": {"temperature": 0.2, "prompt": "go"}}


@pytest.mark.asyncio
@respx.mock
async def test_happy_path() -> None:
    respx.post("http://localhost:9000/chat").mock(
        return_value=httpx.Response(200, json={"message": "done"})
    )
    proxy = HTTPProxy(
        _config(request=RequestConfig(body={"message": "$.payload.task"}))
    )
    result = await proxy.handle_task(_request())
    assert result.output == "done"


@pytest.mark.asyncio
@respx.mock
async def test_params_appended() -> None:
    sent_url: list[str] = []

    def capture(req: httpx.Request) -> httpx.Response:
        sent_url.append(str(req.url))
        return httpx.Response(200, json={"message": "ok"})

    respx.post("http://localhost:9000/chat").mock(side_effect=capture)
    proxy = HTTPProxy(_config(request=RequestConfig(params={"stream": "false"})))
    await proxy.handle_task(_request())
    assert "stream=false" in sent_url[0]


@pytest.mark.asyncio
@respx.mock
async def test_artifacts_extracted() -> None:
    respx.post("http://localhost:9000/chat").mock(
        return_value=httpx.Response(200, json={"message": "text", "report": "content"})
    )
    proxy = HTTPProxy(
        _config(
            response=ResponseConfig(
                output="$.message", artifacts={"report": "$.report"}
            )
        )
    )
    result = await proxy.handle_task(_request())
    assert result.artifacts == {"report": "content"}


@pytest.mark.asyncio
@respx.mock
async def test_upstream_502_raises_adapter_error() -> None:
    respx.post("http://localhost:9000/chat").mock(
        return_value=httpx.Response(502, json={"error": "bad gateway"})
    )
    proxy = HTTPProxy(_config())
    with pytest.raises(AdapterError) as exc_info:
        await proxy.handle_task(_request())
    assert exc_info.value.code == "UPSTREAM_ERROR"
