"""Entry graph — triage and routing.

Calls the planner agent (command="fast") to classify the incoming task.
Returns an uncompiled StateGraph; LangGraph Server compiles and attaches
its own checkpointer per langgraph.json.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph

from ._invoke import invoke_agent
from ._state import EntryState


async def triage_node(state: EntryState) -> dict[str, Any]:
    """Call planner/fast to classify task complexity."""
    result = await invoke_agent(
        "planner",
        command="fast",
        task=state["task"],
        trace_id=state.get("trace_id", ""),
        run_id=state.get("run_id", ""),
    )
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
    graph = StateGraph(EntryState)
    graph.add_node("triage", triage_node)
    graph.set_entry_point("triage")
    graph.add_edge("triage", END)
    return graph
