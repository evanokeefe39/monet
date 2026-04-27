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

    if ":" in model_string:
        provider, model = model_string.split(":", 1)
        return init_chat_model(model, model_provider=provider)
    return init_chat_model(model_string)
