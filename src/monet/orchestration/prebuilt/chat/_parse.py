"""Slash-command parsing and route-after-parse edge."""

from __future__ import annotations

from typing import Any

from ._state import (
    ChatState,  # noqa: TC001 — runtime import for LangGraph get_type_hints()
)


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    """Return the content of the last user message, or empty string."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


def _parse_slash(content: str) -> dict[str, Any]:
    """Parse a slash command. Returns a ``{route, command_meta}`` patch.

    Non-slash input returns ``{"route": None}`` to fall through to triage.
    ``/plan <task>`` → planning. ``/<agent>:<cmd> <task>`` → specialist.
    Anything else on the slash prefix is an inline error routed to chat.
    """
    stripped = content.strip()
    if not stripped.startswith("/"):
        return {"route": None}

    head, _, remainder = stripped.partition(" ")
    remainder = remainder.strip()

    if head == "/plan":
        return {
            "route": "planning",
            "command_meta": {"task": remainder},
        }

    token = head.lstrip("/")
    if ":" in token:
        agent_id, _, mode = token.partition(":")
        if agent_id and mode:
            return {
                "route": "specialist",
                "command_meta": {
                    "specialist": agent_id,
                    "mode": mode,
                    "task": remainder,
                },
            }

    return {
        "route": "chat",
        "command_meta": {"unknown_command": head},
    }


async def parse_command_node(state: ChatState) -> dict[str, Any]:
    """Pure-string slash parser. No LLM call.

    Writes ``task`` into state so the planning subgraph picks it up.
    """
    last = _last_user_message(state.get("messages") or [])
    patch = _parse_slash(last)
    meta = patch.get("command_meta") or {}
    task = meta.get("task") or last
    patch["task"] = task  # type: ignore[typeddict-unknown-key]
    return patch


def _route_after_parse(state: ChatState) -> str:
    route = state.get("route")
    if route is None:
        return "triage"
    if route == "planning":
        return "planning"
    if route == "specialist":
        return "specialist"
    return "respond"
