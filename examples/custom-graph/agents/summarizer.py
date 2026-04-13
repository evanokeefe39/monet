"""Summarizer agent — demonstrates a BYO agent using @agent."""

from monet import agent

summarizer = agent("summarizer")


@summarizer(command="fast")
async def summarize(task: str, context: list[dict[str, str]]) -> str:
    """Summarize the given task and context into a brief overview."""
    context_text = "\n".join(
        entry.get("content", entry.get("summary", "")) for entry in context
    )
    return f"Summary of '{task}': {context_text[:500]}"
