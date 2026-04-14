"""Fast agent — interactive-latency pool.

Demonstrates a cheap, quick-return agent that a ``fast`` pool worker
claims. Does no LLM call so the example runs with no API keys.
"""

from __future__ import annotations

from monet import agent


@agent(agent_id="fast_agent", command="fast", pool="fast")
async def fast_summarize(task: str) -> str:
    """Return a one-line summary of the input task."""
    return f"fast summary: {task[:120]}"
