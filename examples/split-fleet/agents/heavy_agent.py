"""Heavy agent — long-running / batch pool.

Demonstrates a slow agent that a ``heavy`` pool worker claims. The
simulated 5-second delay is the point — it shows that fast tasks don't
block on heavy ones because each pool drains independently.
"""

from __future__ import annotations

import asyncio

from monet import agent


@agent(agent_id="heavy_agent", command="fast", pool="heavy")
async def heavy_process(task: str) -> str:
    """Simulate a long-running compute task."""
    await asyncio.sleep(5)
    return f"heavy result (5s): {task[:120]}"
