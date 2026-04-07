"""Entry graph — triage and routing.

Classifies the incoming user message and sets the triage decision in state.
The top-level run.py sequencer reads the triage result and routes to the
planning graph for complex requests.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph

from monet.orchestration import invoke_agent

from .state import EntryState


async def triage_node(state: EntryState) -> dict[str, Any]:
    """Call sm-planner/fast to classify message complexity."""
    result = await invoke_agent(
        "sm-planner",
        command="fast",
        task=state["user_message"],
        trace_id=state.get("trace_id", ""),
        run_id=state.get("run_id", ""),
    )
    triage = json.loads(result.output) if isinstance(result.output, str) else {}
    return {"triage": triage}


def build_entry_graph() -> StateGraph:
    """Build the entry/triage graph.

    Returns a compiled StateGraph. The caller invokes it and reads
    state["triage"]["complexity"] to decide the next graph.
    """
    graph = StateGraph(EntryState)
    graph.add_node("triage", triage_node)
    graph.set_entry_point("triage")
    graph.add_edge("triage", END)
    return graph
