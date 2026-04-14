"""Demo fan-out graph for the split-fleet example.

Invokes ``fast_agent`` and ``heavy_agent`` in parallel from a single
``task`` input. The two agents run on different pools, so different
worker fleets claim them. Drive with
``monet run --graph demo "<topic>"``.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from monet.orchestration import invoke_agent


class DemoState(TypedDict, total=False):
    task: str
    trace_id: str
    run_id: str
    fast_result: str
    heavy_result: str


async def _run_fast(state: DemoState) -> dict[str, Any]:
    result = await invoke_agent(
        "fast_agent",
        "fast",
        task=state.get("task", ""),
        trace_id=state.get("trace_id"),
        run_id=state.get("run_id"),
    )
    return {"fast_result": str(result.output or "")}


async def _run_heavy(state: DemoState) -> dict[str, Any]:
    result = await invoke_agent(
        "heavy_agent",
        "fast",
        task=state.get("task", ""),
        trace_id=state.get("trace_id"),
        run_id=state.get("run_id"),
    )
    return {"heavy_result": str(result.output or "")}


def build_demo_graph() -> Any:
    """Compile the fan-out demo graph."""
    g: StateGraph[DemoState] = StateGraph(DemoState)
    g.add_node("fast", _run_fast)
    g.add_node("heavy", _run_heavy)
    g.add_edge(START, "fast")
    g.add_edge(START, "heavy")
    g.add_edge("fast", END)
    g.add_edge("heavy", END)
    return g.compile()
