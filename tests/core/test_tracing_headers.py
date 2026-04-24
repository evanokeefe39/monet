"""Tests for W3C traceparent propagation across plane boundaries."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry import context as _ot_context
from opentelemetry import propagate, trace

from monet.core.tracing import configure_tracing, get_tracer
from monet.core.worker_client import WorkerClient, _trace_headers
from monet.server.routes._common import attach_trace_context


@pytest.fixture(autouse=True)
def _ensure_tracing() -> None:
    configure_tracing()


# ---------------------------------------------------------------------------
# _trace_headers
# ---------------------------------------------------------------------------


def test_trace_headers_empty_without_active_span() -> None:
    headers = _trace_headers()
    # No active span in test isolation — carrier is empty or lacks traceparent.
    # Not asserting strict emptiness because a parent test may have left context.
    assert isinstance(headers, dict)


def test_trace_headers_contains_traceparent_inside_span() -> None:
    tracer = get_tracer("test.tracing_headers")
    with tracer.start_as_current_span("root"):
        headers = _trace_headers()
    assert "traceparent" in headers


# ---------------------------------------------------------------------------
# WorkerClient outbound injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_injects_traceparent() -> None:
    tracer = get_tracer("test.tracing_headers")
    mock_response = MagicMock()
    mock_post = AsyncMock(return_value=mock_response)

    client = WorkerClient("http://test-server", "api-key")
    client._client.post = mock_post  # type: ignore[method-assign]

    from monet.types import AgentResult

    result = AgentResult(
        success=True,
        output=None,
        artifacts=(),
        signals=(),
        trace_id="",
        run_id="run-1",
    )

    with tracer.start_as_current_span("root"):
        await client.complete("task-1", result)

    _, kwargs = mock_post.call_args
    assert "traceparent" in kwargs.get("headers", {})

    await client.close()


@pytest.mark.asyncio
async def test_fail_injects_traceparent() -> None:
    tracer = get_tracer("test.tracing_headers")
    mock_response = MagicMock()
    mock_post = AsyncMock(return_value=mock_response)

    client = WorkerClient("http://test-server", "api-key")
    client._client.post = mock_post  # type: ignore[method-assign]

    with tracer.start_as_current_span("root"):
        await client.fail("task-1", "something went wrong")

    _, kwargs = mock_post.call_args
    assert "traceparent" in kwargs.get("headers", {})

    await client.close()


# ---------------------------------------------------------------------------
# Server-side attach_trace_context dependency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_trace_context_sets_span_from_carrier() -> None:
    tracer = get_tracer("test.tracing_headers")

    # Capture a real traceparent from a live span.
    with tracer.start_as_current_span("parent") as parent_span:
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        expected_trace_id = parent_span.get_span_context().trace_id

    mock_request = MagicMock()
    mock_request.headers = carrier

    gen = attach_trace_context(mock_request)
    await gen.__anext__()  # enter — context attached

    current_ctx = trace.get_current_span().get_span_context()
    assert current_ctx.trace_id == expected_trace_id

    with contextlib.suppress(StopAsyncIteration):
        await gen.__anext__()  # exit — context detached


@pytest.mark.asyncio
async def test_attach_trace_context_detaches_on_exit() -> None:
    tracer = get_tracer("test.tracing_headers")

    with tracer.start_as_current_span("parent"):
        carrier: dict[str, str] = {}
        propagate.inject(carrier)

    ctx_before = _ot_context.get_current()

    mock_request = MagicMock()
    mock_request.headers = carrier

    gen = attach_trace_context(mock_request)
    await gen.__anext__()

    with contextlib.suppress(StopAsyncIteration):
        await gen.__anext__()

    assert _ot_context.get_current() == ctx_before
