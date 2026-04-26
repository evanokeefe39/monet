"""Orchestration layer — LangGraph StateGraph integration.

Public surface for custom graph authors:

- ``invoke_agent`` — the single extension point for agent invocation
- State schemas — locked by graph builder implementations
- Graph builders — composable subgraphs for planning, execution, chat
- Lifecycle constants — ``AGENT_*_STATUS`` for progress stream convention
- Queue config — ``configure_queue``, ``get_queue``
"""

from ._invoke import (
    AGENT_COMPLETED_STATUS,
    AGENT_FAILED_STATUS,
    AGENT_STARTED_STATUS,
    configure_capability_index,
    configure_queue,
    get_queue,
    invoke_agent,
)
from ._state import AgentInvocationResult
from .prebuilt import (
    ChatState,
    ExecutionState,
    PlanningState,
    RunState,
    SignalsSummary,
    WaveItem,
    build_chat_graph,
    build_default_graph,
    build_execution_subgraph,
    build_planning_subgraph,
)

__all__ = [
    # Lifecycle convention
    "AGENT_COMPLETED_STATUS",
    "AGENT_FAILED_STATUS",
    "AGENT_STARTED_STATUS",
    # State schemas
    "AgentInvocationResult",
    "ChatState",
    "ExecutionState",
    "PlanningState",
    "RunState",
    "SignalsSummary",
    "WaveItem",
    # Graph builders
    "build_chat_graph",
    "build_default_graph",
    "build_execution_subgraph",
    "build_planning_subgraph",
    "configure_capability_index",
    "configure_queue",
    "get_queue",
    "invoke_agent",
]
