"""Reference publisher agent — final formatting and emission."""

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
    return os.environ.get("MONET_PUBLISHER_MODEL", "google_genai:gemini-2.5-flash")


@agent(agent_id="publisher", command="publish")
async def publisher_publish(task: str) -> str:
    """Format the content for final publication."""
    emit_progress({"status": "publishing", "agent": "publisher"})

    model = _get_model(_model_string())
    prompt = (
        "You are a publisher agent. Take the content and format it as "
        "publication-ready markdown with platform metadata.\n\n"
        f"Content: {task}"
    )
    response = await model.ainvoke([{"role": "user", "content": prompt}])
    content = response.content if hasattr(response, "content") else str(response)
    if isinstance(content, list):
        content = "".join(str(p) for p in content)

    await get_catalogue().write(
        content=content.encode(),
        content_type="text/markdown",
        summary=content[:200],
        confidence=0.9,
        completeness="complete",
    )
    return content
