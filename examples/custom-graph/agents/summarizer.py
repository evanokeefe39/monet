"""Summarizer agent — BYO agent that calls Gemini."""

from __future__ import annotations

import functools
import os
from typing import Any

from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

from monet import agent


@functools.cache
def _model() -> Any:
    model_id = os.environ.get("SUMMARIZER_MODEL", "google_genai:gemini-2.5-flash")
    return init_chat_model(model_id)


summarizer = agent("summarizer")


@summarizer(command="fast")
async def summarize(task: str, context: list[dict[str, str]]) -> str:
    """Produce a short summary for ``task``. System-role entries in
    ``context`` (e.g. tone injected by the before_agent hook) are sent
    as system messages; other entries become user context.
    """
    messages: list[dict[str, str]] = []
    user_context: list[str] = []
    for entry in context:
        if entry.get("role") == "system":
            messages.append({"role": "system", "content": entry.get("content", "")})
        else:
            text = entry.get("content") or entry.get("summary") or ""
            if text:
                user_context.append(text)

    user = f"Summarize the following topic in 3-5 sentences:\n\n{task}"
    if user_context:
        user += "\n\nRelevant context:\n" + "\n".join(user_context)
    messages.append({"role": "user", "content": user})

    response = await _model().ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = "".join(p for p in content if isinstance(p, str))
    return str(content)
