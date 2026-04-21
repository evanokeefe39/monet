"""Orchestration layer — LangGraph StateGraph integration.

Public surface for custom graph authors:

- ``invoke_agent`` — the single extension point for agent invocation
- State schemas — locked by graph builder implementations
- Graph builders — composable subgraphs for planning, execution, chat
- Lifecycle constants — ``AGENT_*_STATUS`` for progress stream convention
- Queue config — ``configure_queue``, ``get_queue``

Server-bootstrap internals (``push_with_retry``, ``close_dispatch_client``,
etc.) are importable but not part of ``__all__``.
"""

from ._invoke import (
    _PUSH_MAX_ATTEMPTS as PUSH_MAX_ATTEMPTS,
)
from ._invoke import (
    AGENT_COMPLETED_STATUS,
    AGENT_FAILED_STATUS,
    AGENT_STARTED_STATUS,
    close_dispatch_client,
    configure_capability_index,
    configure_queue,
    get_queue,
    invoke_agent,
)
from ._invoke import (
    _push_with_retry as push_with_retry,
)
from ._invoke import (
    _write_dispatch_failed as write_dispatch_failed,
)
from ._state import (
    ExecutionState,
    PlanningState,
    RunState,
    SignalsSummary,
    WaveItem,
    WaveResult,
)
from .chat import ChatState, build_chat_graph
from .default_graph import build_default_graph
from .execution_graph import build_execution_subgraph
from .planning_graph import build_planning_subgraph

__all__ = [
    # Lifecycle convention
    "AGENT_COMPLETED_STATUS",
    "AGENT_FAILED_STATUS",
    "AGENT_STARTED_STATUS",
    # Queue + dispatch config
    "PUSH_MAX_ATTEMPTS",
    # State schemas
    "ChatState",
    "ExecutionState",
    "PlanningState",
    "RunState",
    "SignalsSummary",
    "WaveItem",
    "WaveResult",
    # Graph builders
    "build_chat_graph",
    "build_default_graph",
    "build_execution_subgraph",
    "build_planning_subgraph",
    "close_dispatch_client",
    "configure_capability_index",
    "configure_queue",
    "get_queue",
    "invoke_agent",
    "push_with_retry",
    "write_dispatch_failed",
]
