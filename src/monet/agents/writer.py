"""Reference writer agent — content generation."""

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
    return os.environ.get("MONET_WRITER_MODEL", "google_genai:gemini-2.5-flash")


@agent(agent_id="writer", command="deep")
async def writer_deep(task: str) -> str:
    """Generate content based on the brief."""
    emit_progress({"status": "writing", "agent": "writer"})

    model = _get_model(_model_string())
    prompt = (
        "You are a writing agent. Produce polished content for the task. "
        "Match the platform/tone implied by the task.\n\n"
        f"Task: {task}"
    )
    response = await model.ainvoke([{"role": "user", "content": prompt}])
    content = response.content if hasattr(response, "content") else str(response)
    if isinstance(content, list):
        content = "".join(str(p) for p in content)

    await get_catalogue().write(
        content=content.encode(),
        content_type="text/plain",
        summary=content[:200],
        confidence=0.8,
        completeness="complete",
    )
    return content
