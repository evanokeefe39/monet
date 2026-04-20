"""Reference writer agent — content generation."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from monet import agent, emit_progress, get_artifacts, resolve_context
from monet.config._env import agent_model

from .._prompts import extract_text, make_env

_env = make_env(Path(__file__).parent)


@functools.cache
def _get_model(model_string: str) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


def _model_string() -> str:
    return agent_model("writer", "groq:llama-3.1-8b-instant")


writer = agent("writer")


@writer(command="deep")
async def writer_deep(task: str, context: list[dict[str, Any]] | None = None) -> str:
    """Generate polished long-form content from a brief and prior research."""
    emit_progress({"status": "writing", "agent": "writer"})
    context = await resolve_context(context or [])

    prompt = _env.get_template("deep.j2").render(task=task, context=context)
    model = _get_model(_model_string())
    response = await model.ainvoke([{"role": "user", "content": prompt}])
    content = extract_text(response)

    await get_artifacts().write(
        content=content.encode(),
        content_type="text/plain",
        summary=content[:200],
        confidence=0.8,
        completeness="complete",
    )
    return content
