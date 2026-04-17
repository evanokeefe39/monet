"""Custom researcher — canned output, bespoke ID."""

from __future__ import annotations

from monet import agent, emit_progress

from ._stub_llm import canned_response


@agent(agent_id="myco_researcher", command="gather", pool="local")
async def gather(task: str) -> str:
    """Return canned research findings for *task*."""
    emit_progress({"agent": "myco_researcher", "status": "gathering"})
    return canned_response(task, kind="research")
