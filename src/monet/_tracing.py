"""OTel tracing setup. Internal.

The backend is any OTLP-compatible service: Langfuse, LangSmith, SigNoz, etc.
No backend-specific code. Configure via standard OTEL_* environment variables.
"""

from __future__ import annotations

import atexit
import os
import warnings

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider

_provider: TracerProvider | None = None
_exporter_attached: bool = False


def configure_tracing(
    endpoint: str | None = None,
    service_name: str = "monet",
) -> None:
    """Configure OTel tracing. Idempotent — safe to call multiple times.

    Reads OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_SERVICE_NAME from environment.
    """
    global _provider, _exporter_attached

    if _provider is None:
        resource = Resource.create(
            {
                SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", service_name),
                "monet.version": "0.1.0",
            }
        )
        _provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(_provider)
        atexit.register(_provider.shutdown)

    ep = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if ep and not _exporter_attached:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            _exporter_attached = True
        except ImportError:
            warnings.warn(
                "OTEL_EXPORTER_OTLP_ENDPOINT is set but "
                "opentelemetry-exporter-otlp-proto-http is not installed. "
                "Traces will not be exported.",
                stacklevel=2,
            )


def get_tracer(name: str = "monet") -> trace.Tracer:
    """Get an OTel tracer. Auto-configures on first call."""
    if _provider is None:
        configure_tracing()
    return trace.get_tracer(name)
