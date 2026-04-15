"""``search`` — a stub search capability.

Intentionally free of network calls: this example demonstrates agent
REGISTRATION + DISPATCH, not search quality. Swap the body for a real
implementation (Tavily, Exa, web fetch) to get a usable agent.
"""

from __future__ import annotations

from monet import agent

search = agent("search")


@search(command="fast", description="Quick keyword search (stub).")
async def search_fast(task: str) -> dict[str, object]:
    """Return a canned search result for the query in ``task``."""
    return {
        "query": task,
        "results": [
            {
                "title": f"Stub result for: {task}",
                "url": "https://example.invalid/stub",
                "snippet": (
                    "Wire a real search provider here (Tavily, Exa, etc.). "
                    "This agent demonstrates dispatch, not retrieval."
                ),
            }
        ],
    }
