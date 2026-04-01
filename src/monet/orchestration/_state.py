"""Lean LangGraph state schema.

Full artifact content never lives in graph state.
Only summaries, pointers, confidence, and signals.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _append_reducer(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reducer that appends new entries to existing list."""
    return existing + new


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
    needs_human_review: bool
    escalation_requested: bool
    semantic_error: dict[str, str] | None
    trace_id: str
    run_id: str


class GraphState(TypedDict, total=False):
    """Top-level LangGraph state. Always lean."""

    task: str
    trace_id: str
    run_id: str
    results: Annotated[list[dict[str, Any]], _append_reducer]
    needs_review: bool
