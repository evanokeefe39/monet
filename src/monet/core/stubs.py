"""Progress and signal stubs. Internal.

Public surface: monet.emit_progress, monet.emit_signal
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from monet.types import ArtifactPointer, Signal


# ── Signal collector — set by decorator before each invocation ────────────────

_signal_collector: ContextVar[list[Signal] | None] = ContextVar(
    "_signal_collector", default=None
)


# ── Typed progress writer — set by worker before each task execution ──────────
# Typed as Any to avoid a monet.core → monet.queue import cycle.
# At runtime this holds a ProgressWriter instance or None.

_progress_writer_cv: ContextVar[Any] = ContextVar("_progress_writer_cv", default=None)

# Current task_id — set alongside _progress_writer_cv by the worker so the
# decorator can attribute lifecycle events to the correct task.

_current_task_id: ContextVar[str] = ContextVar("_current_task_id", default="")


# ── Progress publisher — set by the worker before each invocation ─────────────
# The worker wraps each task with a publisher that forwards into a bounded
# asyncio.Queue drained asynchronously to task_queue.publish_progress(). This
# lets emit_progress() (a sync call) hand events off without blocking the
# agent on transport I/O.

_progress_publisher: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "_progress_publisher", default=None
)


# ── SDK functions ──────────────────────────────────────────────────────────────


def emit_progress(data: dict[str, Any]) -> None:
    """Emit a progress event.

    Resolution order:
    1. If a worker-side publisher is set in ContextVar, call it.
       The publisher forwards into a bounded queue drained by the worker.
    2. Otherwise, fall back to the LangGraph stream writer (in-graph use).
    3. Otherwise, no-op.
    """
    publisher = _progress_publisher.get()
    if publisher is not None:
        publisher(data)
        return
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        writer(data)
    except (LookupError, RuntimeError):
        pass


def emit_signal(signal: Signal) -> None:
    """Emit a signal alongside the agent result. Non-fatal — agent continues.

    Signals accumulate in the in-memory collector (routed into
    ``AgentResult.signals`` by the decorator) **and** are recorded as an
    OTel span event on the active agent span when one exists. The span
    event is what makes signals queryable from ``otel_query`` after the
    run — meta-agents score agents on signal mix without a duplicate
    ``RunSummary`` artifact.

    No-op outside the @agent decorator context.

    Use NeedsHumanReview / EscalationRequired as exceptions when the agent
    cannot usefully continue. Use emit_signal() when it can return a result
    alongside the signal.
    """
    collector = _signal_collector.get()
    if collector is not None:
        collector.append(signal)
    # Telemetry mirror — intentionally after the routing append so the
    # collector remains the source of truth for in-flight routing.
    from opentelemetry import trace

    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return
    attrs: dict[str, str] = {
        "signal.type": str(signal.get("type", "")),
        "signal.reason": str(signal.get("reason", ""))[:500],
    }
    metadata = signal.get("metadata")
    if isinstance(metadata, dict):
        for k, v in metadata.items():
            if isinstance(v, str | int | float | bool):
                attrs[f"signal.meta.{k}"] = str(v)
    span.add_event("signal", attributes=attrs)


async def write_artifact(
    content: bytes,
    content_type: str,
    **kwargs: Any,
) -> ArtifactPointer:
    """Write content to the artifact store and register the pointer.

    Convenience alias for ``await get_artifacts().write(...)``. Completes
    the ambient trio (emit_progress, emit_signal, write_artifact). The
    pointer is appended to ``AgentResult.artifacts`` automatically.
    """
    from .artifacts import get_artifacts

    return await get_artifacts().write(
        content=content, content_type=content_type, **kwargs
    )
