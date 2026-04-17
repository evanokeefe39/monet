"""Bespoke pipeline — zero reuse of monet.pipelines.default.

A simple two-step pipeline: researcher then writer. Invocable via::

    monet run --graph custom_pipeline "topic"
    MonetClient.run("custom_pipeline", {"task": "topic"})

Demonstrates that `monet.toml [entrypoints.<name>]` is the only coupling
to the server — the graph itself can be any `StateGraph`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from monet.orchestration import invoke_agent

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class MycoPipelineState(TypedDict, total=False):
    task: str
    research_output: str
    draft_output: str


async def research_node(state: MycoPipelineState) -> dict[str, Any]:
    result = await invoke_agent(
        "myco_researcher", command="gather", task=state.get("task", "")
    )
    output = (
        result.output if isinstance(result.output, str) else str(result.output or "")
    )
    return {"research_output": output}


async def draft_node(state: MycoPipelineState) -> dict[str, Any]:
    task = state.get("task", "")
    findings = state.get("research_output", "")
    result = await invoke_agent(
        "myco_writer",
        command="compose",
        task=f"{task}\n\nFindings:\n{findings}",
    )
    output = (
        result.output if isinstance(result.output, str) else str(result.output or "")
    )
    return {"draft_output": output}


def build_custom_pipeline() -> CompiledStateGraph:  # type: ignore[type-arg]
    """Compile the bespoke pipeline."""
    graph: StateGraph[MycoPipelineState] = StateGraph(MycoPipelineState)
    graph.add_node("research", research_node)
    graph.add_node("draft", draft_node)
    graph.add_edge(START, "research")
    graph.add_edge("research", "draft")
    graph.add_edge("draft", END)
    return graph.compile()
