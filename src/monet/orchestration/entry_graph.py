"""Entry graph — triage and routing.

Calls the planner agent (command="fast") to classify the incoming task.
Returns an uncompiled StateGraph; LangGraph Server compiles and attaches
its own checkpointer per langgraph.json.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.runnables import (
    RunnableConfig,  # noqa: TC002 — needed at runtime for LangGraph signature introspection
)
from langgraph.graph import END, StateGraph

from monet.core.tracing import attached_trace, extract_carrier_from_config

from ._invoke import invoke_agent
from ._result_parser import ParseFailure, parse_json_output
from ._state import EntryState

if TYPE_CHECKING:
    from monet.core.hooks import GraphHookRegistry


async def triage_node(state: EntryState, config: RunnableConfig) -> dict[str, Any]:
    """Call planner/fast to classify task complexity."""
    async with attached_trace(extract_carrier_from_config(config)):
        result = await invoke_agent(
            "planner",
            command="fast",
            task=state["task"],
            trace_id=state.get("trace_id", ""),
            run_id=state.get("run_id", ""),
        )
    parsed = parse_json_output(result)
    if isinstance(parsed, ParseFailure):
        # Safe default: assume complex when triage output is unparseable.
        triage: dict[str, Any] = {
            "complexity": "complex",
            "suggested_agents": [],
            "requires_planning": True,
        }
    else:
        triage = parsed
    return {"triage": triage}


def build_entry_graph(
    hooks: GraphHookRegistry | None = None,
) -> StateGraph[EntryState]:
    """Build the triage graph. Returns uncompiled StateGraph.

    Args:
        hooks: Optional graph hook registry. Fires ``after_triage`` with
            the triage dict after classification.
    """
    _triage_inner = triage_node

    async def _triage_with_hooks(
        state: EntryState, config: RunnableConfig
    ) -> dict[str, Any]:
        update = await _triage_inner(state, config)
        if hooks:
            update["triage"] = await hooks.run("after_triage", update["triage"])
        return update

    node = _triage_with_hooks if hooks else triage_node

    graph = StateGraph(EntryState)
    graph.add_node("triage", node)
    graph.set_entry_point("triage")
    graph.add_edge("triage", END)
    return graph
