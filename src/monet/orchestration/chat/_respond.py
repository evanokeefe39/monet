"""Direct-LLM respond node. No ``invoke_agent`` dependency."""

from __future__ import annotations

from typing import Any

from monet.config import ChatConfig

from . import _lc
from ._state import (
    ChatState,  # noqa: TC001 — runtime import for LangGraph get_type_hints()
)


async def respond_node(state: ChatState) -> dict[str, Any]:
    """Direct LLM response. Handles unknown slash commands inline."""
    meta = state.get("command_meta") or {}
    if "unknown_command" in meta:
        cmd = meta["unknown_command"]
        content = (
            f"Unknown command `{cmd}`. Try `/plan <task>` or "
            f"`/<agent>:<command> <task>`."
        )
        return {"messages": [{"role": "assistant", "content": content}]}

    cfg = ChatConfig.load()
    messages = state.get("messages") or []
    payload = _lc._to_langchain(messages)
    clarification = meta.get("clarification_prompt")
    if clarification:
        payload = [
            {"role": "system", "content": str(clarification)},
            *payload,
        ]

    llm = _lc._load_model(cfg.respond_model)
    reply = await llm.ainvoke(payload)
    content = getattr(reply, "content", None) or str(reply)
    return {"messages": [{"role": "assistant", "content": content}]}
