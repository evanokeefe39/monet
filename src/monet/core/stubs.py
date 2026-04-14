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

    Signals accumulate. No-op outside the @agent decorator context.

    Use NeedsHumanReview / EscalationRequired as exceptions when the agent
    cannot usefully continue. Use emit_signal() when it can return a result
    alongside the signal.
    """
    collector = _signal_collector.get()
    if collector is not None:
        collector.append(signal)


async def write_artifact(
    content: bytes,
    content_type: str,
    summary: str,
    confidence: float = 0.0,
    completeness: str = "complete",
    sensitivity_label: str = "internal",
    key: str | None = None,
) -> ArtifactPointer:
    """Write content to the artifact store and register the pointer.

    Convenience alias for ``await get_artifacts().write(...)``. Completes
    the ambient trio (emit_progress, emit_signal, write_artifact). The
    pointer is appended to ``AgentResult.artifacts`` automatically.
    """
    from .artifacts import get_artifacts

    kwargs: dict[str, str] = {}
    if key is not None:
        kwargs["key"] = key
    return await get_artifacts().write(
        content=content,
        content_type=content_type,
        summary=summary,
        confidence=confidence,
        completeness=completeness,
        sensitivity_label=sensitivity_label,
        **kwargs,
    )
