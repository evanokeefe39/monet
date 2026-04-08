"""OTel tracing setup. Internal.

The backend is any OTLP-compatible service: Langfuse, LangSmith, SigNoz, etc.
No backend-specific code. Configure via standard OTEL_* environment variables.
"""

from __future__ import annotations

import atexit
import base64
import os
import warnings

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider

_provider: TracerProvider | None = None
_exporter_attached: bool = False


def _apply_langsmith_shortcut() -> None:
    """LangSmith shortcut: LANGSMITH_API_KEY (+ optional LANGSMITH_PROJECT)."""
    key = os.environ.get("LANGSMITH_API_KEY")
    if not key:
        return
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = (
            "https://api.smith.langchain.com/otel/v1/traces"
        )
    if not os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"):
        headers = f"x-api-key={key}"
        project = os.environ.get("LANGSMITH_PROJECT")
        if project:
            headers += f",Langsmith-Project={project}"
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = headers


def _apply_honeycomb_shortcut() -> None:
    """Honeycomb shortcut: HONEYCOMB_API_KEY (+ optional HONEYCOMB_DATASET)."""
    key = os.environ.get("HONEYCOMB_API_KEY")
    if not key:
        return
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://api.honeycomb.io"
    if not os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"):
        headers = f"x-honeycomb-team={key}"
        dataset = os.environ.get("HONEYCOMB_DATASET")
        if dataset:
            headers += f",x-honeycomb-dataset={dataset}"
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = headers


def _apply_langfuse_shortcut() -> None:
    """If LANGFUSE_PUBLIC_KEY/SECRET_KEY are set and OTEL_EXPORTER_OTLP_ENDPOINT
    is not, derive the OTLP endpoint and Basic auth header from them.

    Convenience for the local docker-compose stack. Explicit OTEL_* vars always
    take precedence — this only fills in gaps.
    """
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        return
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000").rstrip("/")
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"{host}/api/public/otel"
    if not os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"):
        token = base64.b64encode(f"{pk}:{sk}".encode()).decode()
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Basic {token}"


def configure_tracing(
    endpoint: str | None = None,
    service_name: str = "monet",
) -> None:
    """Configure OTel tracing. Idempotent — safe to call multiple times.

    Reads OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_SERVICE_NAME from environment.
    """
    global _provider, _exporter_attached

    _apply_langfuse_shortcut()
    _apply_langsmith_shortcut()
    _apply_honeycomb_shortcut()

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
