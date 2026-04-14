"""OTel tracing setup. Internal.

The backend is any OTLP-compatible service: Langfuse, LangSmith, SigNoz, etc.
No backend-specific code. Configure via standard OTEL_* environment variables.
"""

from __future__ import annotations

import atexit
import threading
import warnings
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from opentelemetry import context as _ot_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from monet.config import ObservabilityConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

    from langchain_core.runnables import RunnableConfig

_provider: TracerProvider | None = None
_exporter_attached: bool = False
_file_exporter_attached: bool = False


class _JsonLinesFileExporter(SpanExporter):
    """Writes each finished span to a JSONL file. Debug-only.

    Intended for local dev loops where OTLP isn't up. Each finished span
    becomes one line of JSON (via :meth:`ReadableSpan.to_json`), appended
    to the configured path under an internal lock.

    Why the lock: :class:`SimpleSpanProcessor.on_end` calls ``export()`` on
    whatever thread ended the span with no internal serialisation, and
    plain ``file.write()`` is not atomic for lines longer than the OS pipe
    buffer. Two concurrent worker spans ending simultaneously would
    interleave and produce JSONL lines that fail ``json.loads``. The lock
    serialises the writes.

    Characteristics:

    - Blocks the thread calling ``span.end()`` for the duration of the
      write. Do not use behind production hot paths.
    - No rotation. File grows unbounded. Intended lifetime is a debugging
      session.
    - File handle is closed on :meth:`shutdown`, which the
      :class:`TracerProvider`'s atexit hook calls. On abrupt process exit
      the OS reclaims the descriptor.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # buffering=1 selects line buffering in text mode so ``tail -f``
        # works even without an explicit force_flush call. The explicit
        # flush() inside the lock is cross-platform belt-and-suspenders.
        self._file = path.open("a", encoding="utf-8", buffering=1)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            if self._file.closed:
                return SpanExportResult.FAILURE
            for span in spans:
                # indent=None → single-line JSON. Requires OTel SDK ≥ 1.20,
                # matching the runtime dep pin in pyproject.toml.
                self._file.write(span.to_json(indent=None) + "\n")
            self._file.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        with self._lock:
            if not self._file.closed:
                self._file.flush()
        return True


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


def configure_tracing(
    config: ObservabilityConfig | None = None,
) -> None:
    """Configure OTel tracing. Idempotent — safe to call multiple times.

    Pass an :class:`~monet.config.ObservabilityConfig` to override where
    endpoint, headers, service name, and the optional debug trace file
    come from. When ``None``, loads from the environment via
    :meth:`ObservabilityConfig.load`.

    The OTLP endpoint and headers are passed directly to
    :class:`OTLPSpanExporter` as constructor kwargs; this function does
    not write to ``os.environ``. Previous releases populated
    ``OTEL_EXPORTER_OTLP_*`` env vars as a side effect — that behaviour
    is gone because it leaked between server / worker / test processes
    running in the same interpreter.
    """
    global _provider, _exporter_attached, _file_exporter_attached

    cfg = config if config is not None else ObservabilityConfig.load()

    if _provider is None:
        resource = Resource.create(
            {
                SERVICE_NAME: cfg.service_name,
                "monet.version": "0.1.0",
            }
        )
        _provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(_provider)
        atexit.register(_provider.shutdown)

    ep, _ = cfg.otlp_endpoint_and_headers()
    if ep and not _exporter_attached:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            exporter = OTLPSpanExporter(endpoint=ep, headers=cfg.otlp_headers_dict())
            _provider.add_span_processor(BatchSpanProcessor(exporter))
            _exporter_attached = True
        except ImportError:
            warnings.warn(
                "Tracing endpoint is configured but "
                "opentelemetry-exporter-otlp-proto-http is not installed. "
                "Traces will not be exported.",
                stacklevel=2,
            )

    if cfg.trace_file is not None and not _file_exporter_attached:
        _provider.add_span_processor(
            SimpleSpanProcessor(_JsonLinesFileExporter(cfg.trace_file))
        )
        _file_exporter_attached = True


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


def extract_carrier_from_config(
    config: RunnableConfig | None,
) -> dict[str, str]:
    """Pull the CLI-side trace carrier out of langgraph run metadata.

    The CLI injects a W3C traceparent carrier into each langgraph
    run's metadata under :data:`TRACE_CARRIER_METADATA_KEY`. Graph
    entry nodes read it via their ``config`` argument and feed it to
    :func:`attached_trace` so downstream agent spans become part of
    the CLI-side root trace instead of each starting a new root.
    Returns ``{}`` when no carrier is present so callers can pass the
    result to ``attached_trace`` unconditionally.
    """
    if not config:
        return {}
    metadata = config.get("metadata") or {}
    carrier = metadata.get(TRACE_CARRIER_METADATA_KEY)
    return dict(carrier) if isinstance(carrier, dict) else {}


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
