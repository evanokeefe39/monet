"""LangChain model binding shims.

Isolated so future adjustments (system prompt prefix, trimming, provider
switching) have a single touch-point, and so tests can patch
``_load_model`` at one stable path.
"""

from __future__ import annotations

from typing import Any


def _load_model(model_string: str) -> Any:
    """Return a LangChain chat model for ``model_string`` (``provider:name``)."""
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


def _to_langchain(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape monet-style ``{role, content}`` dicts for a LangChain model."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role") or "user"
        content = msg.get("content") or ""
        out.append({"role": role, "content": content})
    return out
