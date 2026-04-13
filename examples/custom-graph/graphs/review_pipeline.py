"""Custom graph — a simple review pipeline with its own hook points.

Demonstrates:
- Building a custom LangGraph StateGraph
- Defining graph-level hook points via GraphHookRegistry
- Composing alongside monet's built-in graphs via langgraph.json
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from monet.orchestration import invoke_agent

if TYPE_CHECKING:
    from monet import GraphHookRegistry

logger = logging.getLogger("custom.review_pipeline")


class ReviewState(TypedDict):
    """State schema for the review pipeline."""

    task: str
    trace_id: str
    run_id: str
    draft: str
    review_notes: str
    approved: bool


async def draft_node(state: ReviewState) -> dict[str, Any]:
    """Call the summarizer agent to produce a draft."""
    result = await invoke_agent(
        "summarizer",
        command="fast",
        task=state["task"],
        trace_id=state.get("trace_id", ""),
        run_id=state.get("run_id", ""),
    )
    return {"draft": result.output or ""}


async def review_node(state: ReviewState) -> dict[str, Any]:
    """Simple auto-review: approve if draft is non-empty."""
    draft = state.get("draft", "")
    if draft.strip():
        return {"approved": True, "review_notes": "Draft looks good."}
    return {"approved": False, "review_notes": "Draft is empty."}


def route_after_review(state: ReviewState) -> str:
    return END


def build_review_graph(
    hooks: GraphHookRegistry | None = None,
) -> StateGraph[ReviewState]:
    """Build the review pipeline graph.

    Hook points:
    - ``before_review``: fires before the review node with the draft
    - ``after_review``: fires after the review node with review notes

    Args:
        hooks: Optional graph hook registry for extension.
    """
    _review_inner = review_node

    async def _review_with_hooks(state: ReviewState) -> dict[str, Any]:
        if hooks:
            state = await hooks.run("before_review", state)
        update = await _review_inner(state)
        if hooks:
            update = await hooks.run("after_review", update)
        return update

    node = _review_with_hooks if hooks else review_node

    graph = StateGraph(ReviewState)
    graph.add_node("draft", draft_node)
    graph.add_node("review", node)

    graph.set_entry_point("draft")
    graph.add_edge("draft", "review")
    graph.add_conditional_edges("review", route_after_review, {END: END})
    return graph
