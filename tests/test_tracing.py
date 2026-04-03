"""Tests for OTel tracing utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from monet._tracing import (
    end_span,
    format_traceparent,
    get_tracer,
    parse_traceparent,
    start_agent_span,
)


def test_get_tracer() -> None:
    tracer = get_tracer()
    assert tracer is not None


def test_start_and_end_span() -> None:
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


def test_spans_exported_with_correct_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify spans carry correct attributes when exported."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        SimpleSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )

    import monet._tracing as tracing_mod

    class _Collector(SpanExporter):
        def __init__(self) -> None:
            self.spans: list[object] = []

        def export(self, spans: object) -> SpanExportResult:
            assert isinstance(spans, (list, tuple))
            self.spans.extend(spans)
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            pass

    # Create a fresh provider+tracer and inject it directly into the module,
    # bypassing set_tracer_provider() which can only be called once per process.
    collector = _Collector()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(collector))
    tracer = provider.get_tracer("monet.agent")
    monkeypatch.setattr(tracing_mod, "_tracer", tracer)

    span = start_agent_span(
        agent_id="test-agent",
        command="fast",
        effort="high",
        run_id="r-1",
        trace_id="t-1",
    )
    end_span(span, success=True)

    assert len(collector.spans) == 1

    exported = collector.spans[0]
    assert exported.name == "agent.test-agent.fast"  # type: ignore[union-attr]
    attrs = exported.attributes  # type: ignore[union-attr]
    assert attrs is not None
    assert attrs.get("gen_ai.agent.id") == "test-agent"
    assert attrs.get("gen_ai.agent.command") == "fast"
    assert attrs.get("monet.effort") == "high"
    assert attrs.get("monet.run_id") == "r-1"
    assert attrs.get("monet.trace_id") == "t-1"

    provider.shutdown()
