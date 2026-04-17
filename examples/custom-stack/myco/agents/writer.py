"""Custom writer — canned prose output."""

from __future__ import annotations

from monet import agent, emit_progress

from ._stub_llm import canned_response


@agent(agent_id="myco_writer", command="compose", pool="local")
async def compose(task: str) -> str:
    """Return canned draft prose for *task*."""
    emit_progress({"agent": "myco_writer", "status": "composing"})
    return canned_response(task, kind="write")
