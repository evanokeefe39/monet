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
    RunState,
    SignalsSummary,
    WaveItem,
    WaveResult,
)
from .chat_graph import ChatState, build_chat_graph
from .default_graph import build_default_graph
from .entry_graph import build_entry_subgraph
from .execution_graph import AGENT_FAILED_EVENT_STATUS, build_execution_subgraph
from .planning_graph import build_planning_subgraph

__all__ = [
    "AGENT_FAILED_EVENT_STATUS",
    "ChatState",
    "EntryState",
    "ExecutionState",
    "PlanningState",
    "RunState",
    "SignalsSummary",
    "WaveItem",
    "WaveResult",
    "build_chat_graph",
    "build_default_graph",
    "build_entry_subgraph",
    "build_execution_subgraph",
    "build_planning_subgraph",
    "configure_queue",
    "invoke_agent",
]
