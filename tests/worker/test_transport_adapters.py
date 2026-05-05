"""Tests for HTTP, CLI, and SSE transport adapters."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from monet.worker.execution._protocol import Endpoint
from monet.worker.transport._cli import CLISession, CLITransport
from monet.worker.transport._errors import AgentError, ProtocolError, TransportError
from monet.worker.transport._http import HTTPTransport
from monet.worker.transport._sse import SSETransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monet.worker.transport._protocol import ObservedEvent

# ── Helpers ───────────────────────────────────────────────────────────────────


def _endpoint(address: str = "http://localhost:8080", **meta: Any) -> Endpoint:
    return Endpoint(
        address=address,
        process_id="test-pid",
        backend_type="subprocess",
        metadata=meta,
    )


async def _collect(it: AsyncIterator[ObservedEvent]) -> list[ObservedEvent]:
    return [ev async for ev in it]


# ── HTTP transport ─────────────────────────────────────────────────────────────


def _http_app(
    status: int = 200, body: Any = None, raw: str | None = None
) -> httpx.MockTransport:
    """Return an httpx mock transport that responds to POST /task."""

    def handler(request: httpx.Request) -> httpx.Response:
        if raw is not None:
            return httpx.Response(status, text=raw)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_http_submit_and_receive_result() -> None:
    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    # Patch the client to use a mock transport.
    session._client = httpx.AsyncClient(transport=_http_app(body={"output": "hello"}))

    await session.submit({"task_id": "t1", "payload": {}})
    events = await _collect(session.receive())
    await session.close()

    assert len(events) == 1
    assert events[0].type == "result"
    assert events[0].data["output"] == "hello"
    assert events[0].data["success"] is True
    assert events[0].data["artifacts"] == {}


@pytest.mark.asyncio
async def test_http_4xx_raises_agent_error() -> None:
    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    session._client = httpx.AsyncClient(
        transport=_http_app(
            status=400,
            body={"error": "bad request", "error_code": "INVALID_REQUEST"},
        )
    )

    with pytest.raises(AgentError, match="HTTP 400") as exc_info:
        await session.submit({"task_id": "t1", "payload": {}})
    assert exc_info.value.status_code == 400
    assert "bad request" in exc_info.value.body
    await session.close()


@pytest.mark.asyncio
async def test_http_4xx_structured_error_code_in_message() -> None:
    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    session._client = httpx.AsyncClient(
        transport=_http_app(
            status=422,
            body={"error": "validation failed", "error_code": "INVALID_REQUEST"},
        )
    )

    with pytest.raises(AgentError, match=r"INVALID_REQUEST") as exc_info:
        await session.submit({"task_id": "t1", "payload": {}})
    assert exc_info.value.status_code == 422
    await session.close()


@pytest.mark.asyncio
async def test_http_5xx_raises_agent_error() -> None:
    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    session._client = httpx.AsyncClient(
        transport=_http_app(status=500, raw="internal server error")
    )

    with pytest.raises(AgentError, match="HTTP 500") as exc_info:
        await session.submit({"task_id": "t1", "payload": {}})
    assert exc_info.value.status_code == 500
    assert exc_info.value.body == "internal server error"
    await session.close()


@pytest.mark.asyncio
async def test_http_bad_json_raises_protocol_error() -> None:
    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    session._client = httpx.AsyncClient(transport=_http_app(raw="not-json-at-all"))

    with pytest.raises(ProtocolError, match="not valid JSON"):
        await session.submit({"task_id": "t1", "payload": {}})
    await session.close()


@pytest.mark.asyncio
async def test_http_connection_refused_raises_transport_error() -> None:
    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    session._client = httpx.AsyncClient(transport=httpx.MockTransport(_refuse))

    with pytest.raises(TransportError, match="connection refused"):
        await session.submit({"task_id": "t1", "payload": {}})
    await session.close()


@pytest.mark.asyncio
async def test_http_cancel_is_noop() -> None:
    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    await session.cancel()  # must not raise
    await session.close()


@pytest.mark.asyncio
async def test_http_receive_before_submit_raises_protocol_error() -> None:
    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)

    with pytest.raises(ProtocolError, match="submit"):
        await _collect(session.receive())
    await session.close()


@pytest.mark.asyncio
async def test_http_close_idempotent() -> None:
    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    await session.close()
    await session.close()  # second call must not raise


# ── CLI transport ─────────────────────────────────────────────────────────────

_CLI_ECHO_AGENT = [
    sys.executable,
    "-c",
    (
        "import sys, json\n"
        "payload = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'type': 'result', 'data': {'echo': payload}}))\n"
    ),
]

_CLI_ERROR_AGENT = [
    sys.executable,
    "-c",
    (
        "import sys, json\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'type': 'error', 'message': 'agent failed'}))\n"
    ),
]

_CLI_BAD_JSON_AGENT = [
    sys.executable,
    "-c",
    "import sys; sys.stdin.read(); print('not-json')",
]

_CLI_MULTI_EVENT_AGENT = [
    sys.executable,
    "-c",
    (
        "import sys, json\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'type': 'transport_metric', 'data': {'ms': 1}}))\n"
        "print(json.dumps({'type': 'result', 'data': {'output': 'done'}}))\n"
    ),
]


@pytest.mark.asyncio
async def test_cli_submit_and_receive_result() -> None:
    ep = _endpoint(cmd=_CLI_ECHO_AGENT)
    transport = CLITransport()
    session = await transport.connect(ep)

    payload = {"task_id": "t1", "payload": {"x": 1}}
    await session.submit(payload)
    events = await _collect(session.receive())
    await session.close()

    assert len(events) == 1
    assert events[0].type == "result"
    assert events[0].data["echo"] == payload


@pytest.mark.asyncio
async def test_cli_multi_event_stream() -> None:
    ep = _endpoint(cmd=_CLI_MULTI_EVENT_AGENT)
    transport = CLITransport()
    session = await transport.connect(ep)

    await session.submit({})
    events = await _collect(session.receive())
    await session.close()

    assert len(events) == 2
    assert events[0].type == "transport_metric"
    assert events[1].type == "result"


@pytest.mark.asyncio
async def test_cli_agent_error_event_raises_agent_error() -> None:
    ep = _endpoint(cmd=_CLI_ERROR_AGENT)
    transport = CLITransport()
    session = await transport.connect(ep)

    await session.submit({})
    with pytest.raises(AgentError, match="agent failed"):
        await _collect(session.receive())
    await session.close()


@pytest.mark.asyncio
async def test_cli_bad_json_stdout_raises_protocol_error() -> None:
    ep = _endpoint(cmd=_CLI_BAD_JSON_AGENT)
    transport = CLITransport()
    session = await transport.connect(ep)

    await session.submit({})
    with pytest.raises(ProtocolError, match="not valid JSON"):
        await _collect(session.receive())
    await session.close()


@pytest.mark.asyncio
async def test_cli_missing_cmd_raises_transport_error() -> None:
    ep = _endpoint()  # no cmd in metadata
    transport = CLITransport()
    session = await transport.connect(ep)

    with pytest.raises(TransportError, match="cmd"):
        await session.submit({})
    await session.close()


@pytest.mark.asyncio
async def test_cli_command_not_found_raises_transport_error() -> None:
    ep = _endpoint(cmd=["no-such-binary-xyz"])
    transport = CLITransport()
    session = await transport.connect(ep)

    with pytest.raises(TransportError, match="not found"):
        await session.submit({})
    await session.close()


@pytest.mark.asyncio
async def test_cli_receive_before_submit_raises_protocol_error() -> None:
    ep = _endpoint(cmd=_CLI_ECHO_AGENT)
    session = CLISession(ep)

    with pytest.raises(ProtocolError, match="submit"):
        await _collect(session.receive())
    await session.close()


@pytest.mark.asyncio
async def test_cli_close_idempotent() -> None:
    ep = _endpoint(cmd=_CLI_ECHO_AGENT)
    transport = CLITransport()
    session = await transport.connect(ep)
    await session.close()
    await session.close()


# ── SSE transport ──────────────────────────────────────────────────────────────


def _sse_transport(
    events: list[dict[str, Any]], status: int = 200
) -> httpx.MockTransport:
    """Return a mock transport that streams *events* as SSE data: lines."""

    def handler(request: httpx.Request) -> httpx.Response:
        if status >= 400:
            return httpx.Response(status, text="error body")
        body = "\n".join(f"data: {json.dumps(ev)}\n" for ev in events) + "\n"
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/event-stream"},
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_sse_submit_and_receive_result() -> None:
    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    session._client = httpx.AsyncClient(
        transport=_sse_transport([{"type": "result", "data": {"output": "ok"}}])
    )

    await session.submit({"task_id": "t1", "payload": {}})
    events = await _collect(session.receive())
    await session.close()

    assert len(events) == 1
    assert events[0].type == "result"
    assert events[0].data == {"output": "ok"}


@pytest.mark.asyncio
async def test_sse_multi_event_stream_stops_at_result() -> None:
    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    session._client = httpx.AsyncClient(
        transport=_sse_transport(
            [
                {"type": "transport_metric", "data": {"ms": 5}},
                {"type": "result", "data": {"output": "done"}},
                {"type": "result", "data": {"output": "should not appear"}},
            ]
        )
    )

    await session.submit({})
    events = await _collect(session.receive())
    await session.close()

    assert len(events) == 2
    assert events[0].type == "transport_metric"
    assert events[1].type == "result"
    assert events[1].data["output"] == "done"


@pytest.mark.asyncio
async def test_sse_4xx_raises_agent_error() -> None:
    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    session._client = httpx.AsyncClient(transport=_sse_transport([], status=422))

    with pytest.raises(AgentError, match="HTTP 422"):
        await session.submit({})
    await session.close()


@pytest.mark.asyncio
async def test_sse_bad_json_data_raises_protocol_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="data: not-json\n\n",
            headers={"content-type": "text/event-stream"},
        )

    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    session._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await session.submit({})
    with pytest.raises(ProtocolError, match="not valid JSON"):
        await _collect(session.receive())
    await session.close()


@pytest.mark.asyncio
async def test_sse_error_event_raises_agent_error() -> None:
    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    session._client = httpx.AsyncClient(
        transport=_sse_transport([{"type": "error", "message": "agent exploded"}])
    )

    await session.submit({})
    with pytest.raises(AgentError, match="agent exploded"):
        await _collect(session.receive())
    await session.close()


@pytest.mark.asyncio
async def test_sse_connection_refused_raises_transport_error() -> None:
    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    session._client = httpx.AsyncClient(transport=httpx.MockTransport(_refuse))

    with pytest.raises(TransportError, match="connection refused"):
        await session.submit({})
    await session.close()


@pytest.mark.asyncio
async def test_sse_receive_before_submit_raises_protocol_error() -> None:
    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)

    with pytest.raises(ProtocolError, match="submit"):
        await _collect(session.receive())
    await session.close()


@pytest.mark.asyncio
async def test_sse_close_idempotent() -> None:
    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    await session.close()
    await session.close()


@pytest.mark.asyncio
async def test_sse_cancel_before_response_is_noop() -> None:
    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    await session.cancel()  # no response open — must not raise
    await session.close()


# ── Protocol conformance ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_session_satisfies_session_protocol() -> None:
    from monet.worker.transport._protocol import Session, TransportAdapter

    transport = HTTPTransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    await session.close()

    assert isinstance(transport, TransportAdapter)
    assert isinstance(session, Session)


@pytest.mark.asyncio
async def test_cli_session_satisfies_session_protocol() -> None:
    from monet.worker.transport._protocol import Session, TransportAdapter

    ep = _endpoint(cmd=_CLI_ECHO_AGENT)
    transport = CLITransport()
    session = await transport.connect(ep)
    await session.close()

    assert isinstance(transport, TransportAdapter)
    assert isinstance(session, Session)


@pytest.mark.asyncio
async def test_sse_session_satisfies_session_protocol() -> None:
    from monet.worker.transport._protocol import Session, TransportAdapter

    transport = SSETransport()
    ep = _endpoint()
    session = await transport.connect(ep)
    await session.close()

    assert isinstance(transport, TransportAdapter)
    assert isinstance(session, Session)
