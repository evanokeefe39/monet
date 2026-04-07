"""Reference researcher agent — query synthesis."""

from __future__ import annotations

import functools
import os
from typing import Any

from monet import agent, emit_progress, get_catalogue


@functools.cache
def _get_model(model_string: str) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


def _model_string() -> str:
    return os.environ.get("MONET_RESEARCHER_MODEL", "google_genai:gemini-2.5-flash")


@agent(agent_id="researcher", command="deep")
async def researcher_deep(task: str) -> str:
    """Synthesize research findings on a topic."""
    emit_progress({"status": "researching", "agent": "researcher"})

    model = _get_model(_model_string())
    prompt = (
        "You are a research agent. Provide a concise, factual synthesis "
        f"of what is known about: {task}\n\n"
        "Return markdown with key findings, sources where possible, and gaps."
    )
    response = await model.ainvoke([{"role": "user", "content": prompt}])
    content = response.content if hasattr(response, "content") else str(response)
    if isinstance(content, list):
        content = "".join(str(p) for p in content)

    await get_catalogue().write(
        content=content.encode(),
        content_type="text/markdown",
        summary=content[:200],
        confidence=0.8,
        completeness="complete",
    )
    return content
