"""Tests for OTel tracing utilities."""

from __future__ import annotations

from monet._tracing import (
    format_traceparent,
    get_tracer,
    parse_traceparent,
    start_agent_span,
)


def test_get_tracer() -> None:
    tracer = get_tracer()
    assert tracer is not None


def test_start_and_end_span() -> None:
    from monet._tracing import end_span

    span = start_agent_span(
        agent_id="test-agent",
        command="fast",
        effort="high",
        run_id="r-1",
        trace_id="t-1",
    )
    assert span is not None
    end_span(span, success=True)


def test_start_error_span() -> None:
    from monet._tracing import end_span

    span = start_agent_span(agent_id="err-agent", command="fast")
    end_span(span, success=False, error_message="something failed")


def test_format_traceparent() -> None:
    tp = format_traceparent(
        trace_id="0af7651916cd43dd8448eb211c80319c",
        span_id="b7ad6b7169203331",
    )
    assert tp == "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_parse_traceparent_valid() -> None:
    result = parse_traceparent(
        "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    )
    assert result is not None
    assert result["version"] == "00"
    assert result["trace_id"] == "0af7651916cd43dd8448eb211c80319c"
    assert result["parent_id"] == "b7ad6b7169203331"
    assert result["trace_flags"] == "01"


def test_parse_traceparent_invalid() -> None:
    assert parse_traceparent("invalid") is None
    assert parse_traceparent("") is None
    assert parse_traceparent("00-short-id-01") is None
