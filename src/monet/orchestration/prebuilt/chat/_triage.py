"""Free-form message triage — classify as chat or plan.

The triage classifier returns information, not a decision — it picks
between ``chat`` (answerable directly) and ``plan`` (needs the planner).
Agent selection remains the planner's job.

Uses ``method="json_mode"`` instead of tool calling — Groq and other
fast providers have unreliable tool_call support; json_mode constrains
output format without depending on the provider's tool implementation.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import BaseModel, Field

from monet.config import ChatConfig

from . import _lc
from ._parse import _last_user_message
from ._state import (
    ChatState,  # noqa: TC001 — runtime import for LangGraph get_type_hints()
)

_log = logging.getLogger(__name__)


class ChatTriageResult(BaseModel):
    """Structured output from the triage classifier — information only."""

    route: Literal["chat", "plan"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_needed: bool = False
    clarification_prompt: str | None = None


_TRIAGE_SYSTEM = (
    "You classify the latest user message. Return one of two routes:\n"
    "- 'chat': conversational, informational, or a question answerable "
    "directly in natural language without tools or multi-step work.\n"
    "- 'plan': the user wants something done that requires research, "
    "generation, analysis, tool use, or multiple agent steps — "
    "anything beyond a plain reply.\n\n"
    "Do NOT pick a specific agent or command; that is the planner's "
    "job. If the user's intent is genuinely ambiguous, set "
    "clarification_needed and include a clarification_prompt asking "
    "them to restate. Bias toward 'plan' when unsure between plan "
    "and chat — producing an unnecessary plan (the user can reject "
    "it) is cheaper than silently downgrading a real task to a chat "
    "reply.\n\n"
    "Respond ONLY with a JSON object matching this schema:\n"
    '{"route": "chat" | "plan", "confidence": 0.0-1.0, '
    '"clarification_needed": bool, "clarification_prompt": string | null}'
)


def _triage_payload(
    messages: list[BaseMessage],
) -> list[BaseMessage]:
    """Prepend a system message explaining the chat-vs-plan decision."""
    return [SystemMessage(content=_TRIAGE_SYSTEM), *messages]


class TriageError(Exception):
    """Triage classification failed — must not be swallowed silently."""


async def triage_node(state: ChatState) -> dict[str, Any]:
    """Classify free-form user text as ``chat`` or ``plan``.

    Raises:
        TriageError: LLM call failed or returned unparseable output.
    """
    cfg = ChatConfig.load()
    messages = state.get("messages") or []
    payload = _triage_payload(messages)
    llm = _lc._load_model(cfg.triage_model).with_structured_output(
        ChatTriageResult, method="json_mode"
    )
    try:
        result = await llm.ainvoke(payload)
    except Exception as exc:
        raise TriageError(f"triage LLM call failed: {exc}") from exc
    if not isinstance(result, ChatTriageResult):
        raise TriageError(
            f"triage model returned unexpected type: {type(result).__name__}"
        )

    task = _last_user_message(messages)
    meta: dict[str, Any] = {"task": task}
    if result.clarification_needed and result.clarification_prompt:
        meta["clarification_prompt"] = result.clarification_prompt
        return {"route": "chat", "command_meta": meta, "task": task}
    node_route = "planning" if result.route == "plan" else "chat"
    return {"route": node_route, "command_meta": meta, "task": task}


def _route_after_triage(state: ChatState) -> str:
    route = state.get("route")
    if route == "planning":
        return "planning"
    return "respond"
