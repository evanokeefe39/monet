"""Lean LangGraph state schema.

Full artifact content never lives in graph state.
Only summaries, pointers, confidence, and signals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Sequence

from monet._types import Signal, SignalType


def _append_reducer(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reducer that appends new entries to existing list."""
    return existing + new


# --- Signal helpers for routing functions ---


def has_signal(
    signals: Sequence[Signal | dict[str, Any]], signal_type: SignalType
) -> bool:
    """Check if a signal list contains a signal of the given type."""
    target = signal_type.value if isinstance(signal_type, SignalType) else signal_type
    return any(
        (s.get("type") if isinstance(s, dict) else s["type"]) == target for s in signals
    )


def get_signal(
    signals: Sequence[Signal | dict[str, Any]], signal_type: SignalType
) -> Signal | dict[str, Any] | None:
    """Get the first signal of the given type, or None."""
    target = signal_type.value if isinstance(signal_type, SignalType) else signal_type
    for s in signals:
        s_type = s.get("type") if isinstance(s, dict) else s["type"]
        if s_type == target:
            return s
    return None


class AgentStateEntry(TypedDict, total=False):
    """A single agent result entry in graph state."""

    agent_id: str
    command: str
    effort: str
    output: str
    artifact_url: str
    summary: str
    confidence: float
    completeness: str
    success: bool
    signals: list[dict[str, Any]]
    trace_id: str
    run_id: str


class GraphState(TypedDict, total=False):
    """Top-level LangGraph state. Always lean."""

    task: str
    trace_id: str
    run_id: str
    results: Annotated[list[dict[str, Any]], _append_reducer]
    needs_review: bool
