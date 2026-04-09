"""Orchestration layer — LangGraph StateGraph integration.

State schemas (EntryState, PlanningState, ExecutionState, WaveItem,
WaveResult) are re-exported here as public surface because client code
that wires the graphs directly needs to construct initial state dicts.
These schemas are locked by the graph builder implementations — changing
them is a breaking change to the public API.
"""

from ._invoke import configure_queue, invoke_agent
from ._state import (
    EntryState,
    ExecutionState,
    PlanningState,
    SignalsSummary,
    WaveItem,
    WaveResult,
)
from .entry_graph import build_entry_graph
from .execution_graph import AGENT_FAILED_EVENT_STATUS, build_execution_graph
from .planning_graph import build_planning_graph

__all__ = [
    "AGENT_FAILED_EVENT_STATUS",
    "EntryState",
    "ExecutionState",
    "PlanningState",
    "SignalsSummary",
    "WaveItem",
    "WaveResult",
    "build_entry_graph",
    "build_execution_graph",
    "build_planning_graph",
    "configure_queue",
    "invoke_agent",
]
