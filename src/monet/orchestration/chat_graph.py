"""Chat graph — multi-turn conversational interface.

A minimal graph that maintains a message history and produces
responses via the configured LLM. Returns an uncompiled StateGraph;
Aegra compiles and attaches its own checkpointer.

The graph has no routing or dispatch logic — intent classification
and run dispatch are handled by the CLI layer.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph


def _message_reducer(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append new messages to the history."""
    return existing + new


class ChatState(TypedDict, total=False):
    """State for the chat graph.

    ``messages`` follows the LangGraph messages convention:
    each entry is ``{"role": "user"|"assistant"|"system", "content": "..."}``.
    """

    messages: Annotated[list[dict[str, Any]], _message_reducer]


async def respond_node(state: ChatState) -> dict[str, Any]:
    """Generate a conversational response from the message history.

    Calls the planner agent with the ``chat`` command to produce a
    response. The agent receives the full message history as context.

    If the planner/chat capability is not registered, falls back to
    echoing a placeholder so the graph still completes.
    """
    from monet.orchestration._invoke import invoke_agent

    messages = state.get("messages") or []

    # Build a task string from the most recent user message.
    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    if not last_user_msg:
        return {
            "messages": [{"role": "assistant", "content": ""}],
        }

    # Build context from prior messages (excluding the last user message).
    context_entries: list[dict[str, Any]] = []
    for msg in messages[:-1]:
        context_entries.append(
            {
                "type": "chat_history",
                "role": msg.get("role", "user"),
                "content": str(msg.get("content", ""))[:500],
            }
        )

    result = await invoke_agent(
        "planner",
        command="chat",
        task=last_user_msg,
        context=context_entries,
    )

    response_text = ""
    if result.output:
        response_text = (
            result.output if isinstance(result.output, str) else str(result.output)
        )

    return {
        "messages": [{"role": "assistant", "content": response_text}],
    }


def build_chat_graph() -> StateGraph[ChatState]:
    """Build the chat graph. Returns uncompiled StateGraph.

    Single-node graph: respond -> END. The graph's only job is to
    receive messages, produce a response, and checkpoint state.
    """
    graph = StateGraph(ChatState)
    graph.add_node("respond", respond_node)
    graph.set_entry_point("respond")
    graph.add_edge("respond", END)
    return graph
