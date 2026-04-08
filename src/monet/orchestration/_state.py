"""Lean LangGraph state schema.

Full artifact content never lives in graph state.
Only summaries, pointers, confidence, and signals.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from monet.types import Signal, SignalType  # noqa: F401


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


# --- Three-graph supervisor topology state schemas ---


class EntryState(TypedDict, total=False):
    """State for the entry/triage graph."""

    task: str
    triage: dict[str, Any] | None
    trace_id: str
    run_id: str


class PlanningState(TypedDict, total=False):
    """State for the planning graph with HITL approval loop."""

    task: str
    work_brief: dict[str, Any] | None
    planning_context: Annotated[list[dict[str, Any]], _append_reducer]
    human_feedback: str | None
    plan_approved: bool | None
    revision_count: int
    trace_id: str
    run_id: str


class ExecutionState(TypedDict, total=False):
    """State for the wave-based execution graph."""

    work_brief: dict[str, Any]
    current_phase_index: int
    current_wave_index: int
    wave_results: Annotated[list[dict[str, Any]], _append_reducer]
    wave_reflections: list[dict[str, Any]]
    completed_phases: list[int]
    signals: dict[str, Any] | None
    abort_reason: str | None
    revision_count: int
    trace_id: str
    run_id: str
    pending_context: list[dict[str, Any]]
    # W3C trace context carrier (traceparent/tracestate) stashed by
    # load_plan so agent_node can re-attach it and make every agent
    # span a child of the root execution span instead of its own root.
    trace_carrier: dict[str, str]


class WaveItem(TypedDict, total=False):
    """A single work item dispatched via Send to agent_node.

    ``context`` carries resolved upstream outputs so each agent can see what
    prior waves produced. The orchestrator builds it in ``dispatch_wave``;
    individual agents receive it via the standard ``context`` parameter.

    ``trace_carrier`` carries the W3C trace context from the execution
    graph's root span so agent_node can re-attach it and make every
    agent span part of a single Langfuse trace.
    """

    agent_id: str
    command: str
    task: str
    phase_index: int
    wave_index: int
    item_index: int
    trace_id: str
    run_id: str
    context: list[dict[str, Any]]
    trace_carrier: dict[str, str]


class WaveResult(TypedDict):
    """Result from a single agent invocation within a wave.

    ``output`` and ``artifacts`` are distinct fields. ``output`` is the
    inline result (string or structured dict). ``artifacts`` lists the
    catalogue pointers written by the agent. The orchestrator reads
    them as separate concerns — no fallback between them.
    """

    phase_index: int
    wave_index: int
    item_index: int
    agent_id: str
    command: str
    output: str | dict[str, Any] | None
    artifacts: list[dict[str, Any]]
    signals: list[dict[str, Any]]
