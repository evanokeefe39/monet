"""Reference publisher agent — final formatting and emission."""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

from monet import agent, emit_progress, get_artifacts, resolve_context

from .._prompts import extract_text, make_env

_env = make_env(Path(__file__).parent)


@functools.cache
def _get_model(model_string: str) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


def _model_string() -> str:
    return os.environ.get("MONET_PUBLISHER_MODEL", "google_genai:gemini-2.5-flash")


publisher = agent("publisher")


@publisher(command="publish")
async def publisher_publish(
    task: str, context: list[dict[str, Any]] | None = None
) -> str:
    """Format upstream content as publication-ready markdown."""
    emit_progress({"status": "publishing", "agent": "publisher"})
    context = await resolve_context(context or [])

    prompt = _env.get_template("publish.j2").render(task=task, context=context)
    model = _get_model(_model_string())
    response = await model.ainvoke([{"role": "user", "content": prompt}])
    content = extract_text(response)

    await get_artifacts().write(
        content=content.encode(),
        content_type="text/markdown",
        summary=content[:200],
        confidence=0.9,
        completeness="complete",
    )
    return content
