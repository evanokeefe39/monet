"""Progress and signal stubs. Internal.

Public surface: monet.emit_progress, monet.emit_signal
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import Signal


# ── Signal collector — set by decorator before each invocation ────────────────

_signal_collector: ContextVar[list[Signal] | None] = ContextVar(
    "_signal_collector", default=None
)


# ── SDK functions ──────────────────────────────────────────────────────────────


def emit_progress(data: dict[str, Any]) -> None:
    """Emit a progress event into the LangGraph stream.

    No-op outside the LangGraph execution context.
    Python 3.11+ required for correct async context propagation.
    """
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        writer(data)
    except Exception:
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
