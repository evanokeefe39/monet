"""Custom conversationalist — drives the chat graph's respond path."""

from __future__ import annotations

from monet import agent

from ._stub_llm import canned_response


@agent(agent_id="myco_conversationalist", command="reply", pool="local")
async def reply(task: str) -> str:
    """Return a canned conversational reply for *task*."""
    return canned_response(task, kind="respond")
