"""OTel tracing setup. Internal.

The backend is any OTLP-compatible service: Langfuse, LangSmith, SigNoz, etc.
No backend-specific code. Configure via standard OTEL_* environment variables.
"""

from __future__ import annotations

import atexit
import base64
import os
import warnings
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from opentelemetry import context as _ot_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_provider: TracerProvider | None = None
_exporter_attached: bool = False


# ── Cross-boundary wire constants ─────────────────────────────────────
#
# These strings are contracts between the CLI and the server, and
# between orchestration and display. Referencing them as constants on
# both ends means a typo can only break one side and fails loudly at
# import / type-check time rather than silently at runtime.

#: Key under which the CLI stashes a W3C traceparent carrier in each
#: langgraph run's metadata. Server-side graph entry nodes read it via
#: ``config["metadata"][TRACE_CARRIER_METADATA_KEY]``.
TRACE_CARRIER_METADATA_KEY = "monet_trace_carrier"

#: Span name for the CLI-side root span that groups a whole monet run.
RUN_ROOT_SPAN_NAME = "monet.run"

#: Span name for the execution graph's in-process root span.
EXECUTION_ROOT_SPAN_NAME = "monet.execution"


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


def inject_trace_context() -> dict[str, str]:
    """Capture the current OTel trace context as a W3C carrier dict.

    Returned dict is safe to stash in LangGraph state (JSON-serialisable)
    and pass between nodes. A downstream node calls
    :func:`extract_and_attach_trace_context` to re-establish the context
    before opening child spans — that makes all child spans part of the
    same trace even though the original parent span is no longer live
    in the current asyncio task.
    """
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def extract_and_attach_trace_context(carrier: dict[str, str]) -> object:
    """Inverse of :func:`inject_trace_context`. Returns an opaque token
    that must be passed to :func:`detach_trace_context` in a finally
    block once the child work completes.

    Prefer :func:`attached_trace` (async context manager) over this
    low-level pair at new call sites — the CM makes the detach
    unforgettable.
    """
    ctx = propagate.extract(carrier)
    return _ot_context.attach(ctx)


def detach_trace_context(token: object) -> None:
    """Pop a previously-attached trace context. Must be called in a
    ``finally`` block paired with :func:`extract_and_attach_trace_context`."""
    _ot_context.detach(token)  # type: ignore[arg-type]


@asynccontextmanager
async def attached_trace(
    carrier: dict[str, str] | None,
) -> AsyncIterator[None]:
    """Async CM that attaches a W3C trace carrier for the duration of
    the block and detaches it on exit — including exceptional exits.

    Usage::

        async with attached_trace(carrier):
            result = await invoke_agent(...)

    When ``carrier`` is empty or ``None``, the CM is a no-op: no
    attach, no detach. This keeps call sites free of
    ``if carrier:`` gates and guarantees the detach cannot be
    forgotten. Prefer this over the raw
    :func:`extract_and_attach_trace_context` / :func:`detach_trace_context`
    pair — four identical try/finally blocks across the orchestration
    modules collapse to one ``async with`` each.
    """
    if not carrier:
        yield
        return
    token = extract_and_attach_trace_context(carrier)
    try:
        yield
    finally:
        detach_trace_context(token)
