"""Tests for OTel tracing utilities."""

from __future__ import annotations

from monet._tracing import configure_tracing, get_tracer


def test_get_tracer() -> None:
    tracer = get_tracer()
    assert tracer is not None


def test_get_tracer_with_name() -> None:
    tracer = get_tracer("my.module")
    assert tracer is not None


def test_configure_tracing_idempotent() -> None:
    """configure_tracing() can be called multiple times safely."""
    configure_tracing()
    configure_tracing()
    configure_tracing(service_name="custom")


def test_tracer_creates_span() -> None:
    """Tracer can create spans via context manager."""
    tracer = get_tracer("monet.agent")
    with tracer.start_as_current_span(
        "agent.test.fast",
        attributes={"agent.id": "test", "agent.command": "fast"},
    ) as span:
        span.set_attribute("agent.success", True)
    # No assertion needed — just verify no exceptions
