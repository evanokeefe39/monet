"""OpenTelemetry tracing utilities.

OTel is a hard dependency. Spans are always created. When
OTEL_EXPORTER_OTLP_ENDPOINT is not set, spans go to a no-op exporter.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import StatusCode

if TYPE_CHECKING:
    from opentelemetry.trace import Span, Tracer

# W3C traceparent format: version-trace_id-parent_id-trace_flags
_TRACEPARENT_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)

_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """Get or create the monet tracer."""
    global _tracer
    if _tracer is None:
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            # No provider configured — set up a basic one
            provider = TracerProvider()
            trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("monet.agent")
    return _tracer


def start_agent_span(
    agent_id: str,
    command: str,
    effort: str | None = None,
    run_id: str = "",
    trace_id: str = "",
    sensitivity_label: str = "internal",
) -> Span:
    """Start an OTel span for an agent invocation.

    Attributes follow gen_ai.* semantic conventions where applicable.
    """
    tracer = get_tracer()
    span = tracer.start_span(
        name=f"agent.{agent_id}.{command}",
        attributes={
            "gen_ai.agent.id": agent_id,
            "gen_ai.agent.command": command,
            "monet.effort": effort or "",
            "monet.run_id": run_id,
            "monet.trace_id": trace_id,
            "monet.sensitivity_label": sensitivity_label,
        },
    )
    return span


def end_span(
    span: Span,
    success: bool,
    error_message: str = "",
) -> None:
    """End an OTel span with status."""
    if success:
        span.set_status(StatusCode.OK)
    else:
        span.set_status(StatusCode.ERROR, error_message)
    span.end()


def format_traceparent(
    trace_id: str,
    span_id: str,
    trace_flags: str = "01",
) -> str:
    """Format a W3C traceparent header value."""
    return f"00-{trace_id}-{span_id}-{trace_flags}"


def parse_traceparent(
    header: str,
) -> dict[str, str] | None:
    """Parse a W3C traceparent header.

    Returns dict with version, trace_id, parent_id, trace_flags
    or None if invalid.
    """
    match = _TRACEPARENT_RE.match(header)
    if not match:
        return None
    return {
        "version": match.group(1),
        "trace_id": match.group(2),
        "parent_id": match.group(3),
        "trace_flags": match.group(4),
    }


def inject_traceparent(headers: dict[str, Any], trace_id: str) -> None:
    """Inject traceparent header for outbound HTTP calls."""
    if trace_id:
        headers["traceparent"] = trace_id
