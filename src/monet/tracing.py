"""Public tracing API — stable re-exports from the internal module.

Import as ``from monet.tracing import ...``. Not re-exported at the
top-level ``monet`` namespace to avoid pulling opentelemetry on
``import monet``.
"""

from monet.core.tracing import (
    EXECUTION_ROOT_SPAN_NAME,
    RUN_ROOT_SPAN_NAME,
    TRACE_CARRIER_METADATA_KEY,
    attached_trace,
    configure_tracing,
    extract_carrier_from_config,
    get_tracer,
    inject_trace_context,
)

__all__ = [
    "EXECUTION_ROOT_SPAN_NAME",
    "RUN_ROOT_SPAN_NAME",
    "TRACE_CARRIER_METADATA_KEY",
    "attached_trace",
    "configure_tracing",
    "extract_carrier_from_config",
    "get_tracer",
    "inject_trace_context",
]
