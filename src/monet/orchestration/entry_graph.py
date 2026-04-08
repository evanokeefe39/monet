"""Entry graph — triage and routing.

Calls the planner agent (command="fast") to classify the incoming task.
Returns an uncompiled StateGraph; LangGraph Server compiles and attaches
its own checkpointer per langgraph.json.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import (
    RunnableConfig,  # noqa: TC002 — needed at runtime for LangGraph signature introspection
)
from langgraph.graph import END, StateGraph

from monet._tracing import (
    detach_trace_context,
    extract_and_attach_trace_context,
)

from ._invoke import extract_carrier_from_config, invoke_agent
from ._state import EntryState
from ._validate import _assert_registered


async def triage_node(state: EntryState, config: RunnableConfig) -> dict[str, Any]:
    """Call planner/fast to classify task complexity."""
    carrier = extract_carrier_from_config(config)
    token = extract_and_attach_trace_context(carrier) if carrier else None
    try:
        result = await invoke_agent(
            "planner",
            command="fast",
            task=state["task"],
            trace_id=state.get("trace_id", ""),
            run_id=state.get("run_id", ""),
        )
    finally:
        if token is not None:
            detach_trace_context(token)
    try:
        triage = json.loads(result.output) if isinstance(result.output, str) else {}
    except json.JSONDecodeError:
        triage = {
            "complexity": "complex",
            "suggested_agents": [],
            "requires_planning": True,
        }
    return {"triage": triage}


def build_entry_graph() -> StateGraph[EntryState]:
    """Build the triage graph. Returns uncompiled StateGraph."""
    _assert_registered("planner", "fast")
    graph = StateGraph(EntryState)
    graph.add_node("triage", triage_node)
    graph.set_entry_point("triage")
    graph.add_edge("triage", END)
    return graph
