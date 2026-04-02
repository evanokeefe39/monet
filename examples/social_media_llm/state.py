"""State schemas for the three-graph supervisor topology.

EntryState   — triage and routing
PlanningState — iterative plan construction with HITL approval
ExecutionState — wave-based parallel execution with QA reflection

WaveItem and WaveResult are data transfer types, not graph state.
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

# ---------------------------------------------------------------------------
# Entry Graph State
# ---------------------------------------------------------------------------


class EntryState(TypedDict, total=False):
    """State for the entry/triage graph."""

    user_message: str
    triage: dict[str, Any] | None
    trace_id: str
    run_id: str


# ---------------------------------------------------------------------------
# Planning Graph State
# ---------------------------------------------------------------------------


class PlanningState(TypedDict, total=False):
    """State for the planning graph.

    planning_context accumulates research artifacts across planner/research
    loops via the append reducer (operator.add).
    """

    user_message: str
    work_brief: dict[str, Any] | None
    planning_context: Annotated[list[dict[str, Any]], add]
    human_feedback: str | None
    plan_approved: bool | None
    revision_count: int
    trace_id: str
    run_id: str


# ---------------------------------------------------------------------------
# Execution Graph State
# ---------------------------------------------------------------------------


class ExecutionState(TypedDict, total=False):
    """State for the execution graph.

    wave_results accumulates results from parallel Send invocations
    via the append reducer (operator.add).
    """

    work_brief: dict[str, Any]
    current_phase_index: int
    current_wave_index: int
    wave_results: Annotated[list[dict[str, Any]], add]
    wave_reflections: list[dict[str, Any]]
    completed_phases: list[int]
    signals: dict[str, Any] | None
    abort_reason: str | None
    revision_count: int
    trace_id: str
    run_id: str


# ---------------------------------------------------------------------------
# Data Transfer Types (not graph state — passed via Send / stored in lists)
# ---------------------------------------------------------------------------


class WaveItem(TypedDict):
    """A single work item dispatched via Send to agent_node.

    This is NOT part of graph state. Each Send target receives one WaveItem.
    """

    agent_id: str
    command: str
    task: str
    phase_index: int
    wave_index: int
    item_index: int
    trace_id: str
    run_id: str


class WaveResult(TypedDict):
    """Result from a single agent invocation within a wave.

    Accumulated in ExecutionState.wave_results via append reducer.
    """

    phase_index: int
    wave_index: int
    item_index: int
    agent_id: str
    command: str
    output: str
    signals: dict[str, Any]
