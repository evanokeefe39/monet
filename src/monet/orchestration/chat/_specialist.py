"""Specialist invocation node — ``/agent:mode`` slash command handler."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import (
    RunnableConfig,  # noqa: TC002 — runtime import for LangGraph signature introspection
)

from monet.orchestration._invoke import invoke_agent

from ._format import _format_agent_result
from ._parse import _last_user_message
from ._state import (
    ChatState,  # noqa: TC001 — runtime import for LangGraph get_type_hints()
)

_log = logging.getLogger(__name__)


def _build_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pack the transcript (minus the last user message) as agent context.

    The receiving agent owns any truncation, summarisation, or filtering
    — the graph always forwards the full history so agent-side decisions
    are first-class, not framework-imposed.
    """
    return [
        {
            "type": "chat_history",
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        }
        for msg in messages[:-1]
    ]


async def specialist_node(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """Invoke a named specialist agent parsed from the slash command."""
    meta = state.get("command_meta") or {}
    agent_id = str(meta.get("specialist") or "").strip()
    if not agent_id:
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": "No specialist name provided.",
                }
            ]
        }
    mode = str(meta.get("mode") or "fast")
    task = str(meta.get("task") or _last_user_message(state.get("messages") or []))
    messages = state.get("messages") or []
    configurable = (config or {}).get("configurable") or {}
    thread_id = (
        configurable.get("thread_id") if isinstance(configurable, dict) else None
    )
    try:
        result = await invoke_agent(
            agent_id,
            command=mode,
            task=task,
            context=_build_context(messages),
            thread_id=thread_id if isinstance(thread_id, str) else None,
        )
    except Exception as exc:
        _log.warning(
            "specialist invocation failed",
            extra={
                "agent_id": agent_id,
                "mode": mode,
                "exc_type": type(exc).__name__,
                "exc_msg": str(exc)[:200],
            },
        )
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": f"Agent `{agent_id}/{mode}` unavailable: {exc}",
                }
            ]
        }
    return {
        "messages": [_format_agent_result(result, label=f"{agent_id}/{mode}")],
    }
